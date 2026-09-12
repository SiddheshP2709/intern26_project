"""
Unit Tests for ETL Pipeline: Ingestion, Transformation, and Loader Modules.
"""
import pytest
from datetime import date, time, datetime, timedelta
import pandas as pd
from pathlib import Path

from pipeline.ingestion import ExcelIngestionEngine
from pipeline.transformation import LogisticsDataTransformer
from pipeline.loader import DatabaseLoader
from database.connection import init_db, get_session
from database.models import Trip


@pytest.fixture
def transformer():
    return LogisticsDataTransformer()


@pytest.fixture
def memory_db_loader():
    test_db_url = "sqlite:///:memory:"
    loader = DatabaseLoader(db_url=test_db_url)
    return loader


class TestTransformationEngine:

    def test_parse_date_formats(self, transformer):
        # String ISO
        assert transformer._parse_date("2026-03-01") == date(2026, 3, 1)
        # String DD-MM-YYYY
        assert transformer._parse_date("01-03-2026") == date(2026, 3, 1)
        # String DD/MM/YYYY
        assert transformer._parse_date("01/03/2026") == date(2026, 3, 1)
        # Datetime object
        assert transformer._parse_date(datetime(2026, 3, 1, 10, 30)) == date(2026, 3, 1)
        # Invalid / None
        assert transformer._parse_date(None) is None
        assert transformer._parse_date("") is None

    def test_parse_time_formats(self, transformer):
        # HH:MM string
        assert transformer._parse_time("06:30") == time(6, 30)
        # HH:MM:SS string
        assert transformer._parse_time("18:45:00") == time(18, 45)
        # 12-hour AM/PM string
        assert transformer._parse_time("6:15 PM") == time(18, 15)
        # Excel decimal fraction (0.5 = 12:00:00)
        assert transformer._parse_time(0.5) == time(12, 0)
        # Dot notation (14.30)
        assert transformer._parse_time("14.30") == time(14, 30)
        # Invalid
        assert transformer._parse_time(None) is None

    def test_shift_standardization(self, transformer):
        assert transformer._standardize_shift("A") == "Shift A"
        assert transformer._standardize_shift("shift b") == "Shift B"
        assert transformer._standardize_shift("SHIFT_C") == "Shift C"
        # Infer from time
        assert transformer._standardize_shift(None, start_time=time(7, 30)) == "Shift A"
        assert transformer._standardize_shift(None, start_time=time(16, 0)) == "Shift B"
        assert transformer._standardize_shift(None, start_time=time(23, 15)) == "Shift C"

    def test_midnight_rollover_calculation(self, transformer):
        """
        Verify that a night shift trip crossing midnight (e.g. 23:45 to 02:15)
        correctly rolls over the calendar day and produces positive durations.
        """
        raw_data = pd.DataFrame([{
            "sr_no": 1,
            "trailer_no": "KA-34-A-1001",
            "date": "2026-03-01",
            "agency": "Guru",
            "arriving_agency": "23:30",
            "leaving_agency": "00:15",       # +45m loading (crosses midnight)
            "arriving_waiting": "00:30",     # +15m transit
            "leaving_waiting": "01:30",      # +60m waiting
            "arriving_fg_yard": "01:45",     # +15m transit
            "leaving_fg_yard": "02:15",      # +30m unloading
            "remarks": "Night dispatch",
            "shift": "Shift C"
        }])

        df_out = transformer.transform(raw_data)
        assert len(df_out) == 1
        row = df_out.iloc[0]

        assert row["loading_time_min"] == 45.0
        assert row["waiting_time_min"] == 60.0
        assert row["unloading_time_min"] == 30.0
        assert row["total_cycle_time_min"] == 165.0  # 23:30 to 02:15 = 2h 45m = 165m
        assert bool(row["is_shift_c_bottleneck"]) is True
        assert bool(row["is_loading_delayed"]) is False

    def test_direct_routing_without_waiting_area(self, transformer):
        """
        Verify that trips bypassing the waiting area (direct to FG yard) are computed properly.
        """
        raw_data = pd.DataFrame([{
            "sr_no": 2,
            "trailer_no": "MH-12-Q-5500",
            "date": "2026-03-01",
            "agency": "Tharini",
            "arriving_agency": "08:00",
            "leaving_agency": "08:35",
            "arriving_waiting": None,
            "leaving_waiting": None,
            "arriving_fg_yard": "08:50",
            "leaving_fg_yard": "09:20",
            "remarks": "Direct bay clearance",
            "shift": "Shift A"
        }])

        df_out = transformer.transform(raw_data)
        row = df_out.iloc[0]

        assert row["loading_time_min"] == 35.0
        assert row["waiting_time_min"] == 0.0
        assert row["unloading_time_min"] == 30.0
        assert row["total_cycle_time_min"] == 80.0
        assert bool(row["is_loading_delayed"]) is False


class TestDatabaseLoader:

    def test_database_persistence_and_query(self, memory_db_loader, transformer):
        raw_data = pd.DataFrame([
            {
                "sr_no": 1,
                "trailer_no": "KA-34-A-1001",
                "date": "2026-03-01",
                "agency": "Guru",
                "arriving_agency": "06:00",
                "leaving_agency": "06:40",
                "arriving_waiting": "06:50",
                "leaving_waiting": "07:05",
                "arriving_fg_yard": "07:15",
                "leaving_fg_yard": "07:45",
                "remarks": "Normal",
                "shift": "Shift A"
            },
            {
                "sr_no": 2,
                "trailer_no": "KA-35-B-2002",
                "date": "2026-03-01",
                "agency": "Sudha",
                "arriving_agency": "08:00",
                "leaving_agency": "09:00",  # 60m loading (>45m benchmark)
                "arriving_waiting": "09:10",
                "leaving_waiting": "09:30",
                "arriving_fg_yard": "09:40",
                "leaving_fg_yard": "10:10",
                "remarks": "Delay",
                "shift": "Shift A"
            }
        ])

        df_clean = transformer.transform(raw_data)
        stats = memory_db_loader.load(df_clean)

        assert stats["inserted"] == 2
        assert stats["updated"] == 0
        assert memory_db_loader.get_trip_count() == 2

        # Test querying
        df_fetched = memory_db_loader.fetch_trips_dataframe()
        assert len(df_fetched) == 2
        assert "trailer_no" in df_fetched.columns

        # Test deduplication on reload
        stats2 = memory_db_loader.load(df_clean)
        assert stats2["inserted"] == 0
        assert stats2["updated"] == 2
        assert memory_db_loader.get_trip_count() == 2

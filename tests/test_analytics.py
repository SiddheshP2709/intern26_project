"""
Unit Tests for Operational Analytics & KPI Engine.
"""
import pytest
from datetime import date, datetime
import pandas as pd
from analytics.metrics import LogisticsAnalyticsEngine


@pytest.fixture
def sample_analytics_dataframe():
    """
    Create a controlled synthetic dataset across Shifts A, B, and C with known agency benchmarks.
    """
    records = []
    
    # 10 trips in Shift A (Average cycle ~90m, wait ~15m, load ~35m - Guru)
    for i in range(10):
        records.append({
            "id": i + 1,
            "trip_uid": f"uid_a_{i}",
            "sr_no": i + 1,
            "trailer_no": f"TR-A-{i}",
            "trip_date": date(2026, 3, 1),
            "shift": "Shift A",
            "agency": "Guru",
            "arriving_agency": datetime(2026, 3, 1, 6 + (i % 6), 0),
            "leaving_agency": datetime(2026, 3, 1, 6 + (i % 6), 35),
            "arriving_waiting": datetime(2026, 3, 1, 6 + (i % 6), 45),
            "leaving_waiting": datetime(2026, 3, 1, 7 + (i % 6), 0),
            "arriving_fg_yard": datetime(2026, 3, 1, 7 + (i % 6), 10),
            "leaving_fg_yard": datetime(2026, 3, 1, 7 + (i % 6), 30),
            "loading_time_min": 35.0,
            "waiting_time_min": 15.0,
            "unloading_time_min": 20.0,
            "transit_time_min": 20.0,
            "total_cycle_time_min": 90.0,
            "is_loading_delayed": False,
            "is_shift_c_bottleneck": False,
            "remarks": "Normal"
        })

    # 10 trips in Shift C (Severe bottleneck: wait ~60m, cycle ~150m, load ~55m - Sudha)
    for i in range(10):
        records.append({
            "id": i + 11,
            "trip_uid": f"uid_c_{i}",
            "sr_no": i + 11,
            "trailer_no": f"TR-C-{i}",
            "trip_date": date(2026, 3, 1),
            "shift": "Shift C",
            "agency": "Sudha",
            "arriving_agency": datetime(2026, 3, 1, 22, 0),
            "leaving_agency": datetime(2026, 3, 1, 22, 55),
            "arriving_waiting": datetime(2026, 3, 1, 23, 10),
            "leaving_waiting": datetime(2026, 3, 2, 0, 10),
            "arriving_fg_yard": datetime(2026, 3, 2, 0, 25),
            "leaving_fg_yard": datetime(2026, 3, 2, 0, 30),
            "loading_time_min": 55.0,    # Breach > 45m
            "waiting_time_min": 60.0,    # +300% surge vs Shift A
            "unloading_time_min": 20.0,
            "transit_time_min": 15.0,
            "total_cycle_time_min": 150.0, # +66.7% surge vs Shift A
            "is_loading_delayed": True,
            "is_shift_c_bottleneck": True,
            "remarks": "Yard congestion"
        })

    return pd.DataFrame(records)


class TestAnalyticsEngine:

    def test_executive_summary_overview(self, sample_analytics_dataframe):
        engine = LogisticsAnalyticsEngine(sample_analytics_dataframe)
        summary = engine.get_summary_overview()

        assert summary["total_trips"] == 20
        assert summary["avg_cycle_time_min"] == 120.0
        assert summary["avg_loading_time_min"] == 45.0
        assert summary["avg_waiting_time_min"] == 37.5
        assert summary["delayed_loading_trips"] == 10
        assert summary["delayed_loading_pct"] == 50.0
        assert summary["shift_c_bottleneck_trips"] == 10
        assert summary["shift_c_bottleneck_pct"] == 50.0

    def test_shift_stratification_bottleneck(self, sample_analytics_dataframe):
        engine = LogisticsAnalyticsEngine(sample_analytics_dataframe)
        shift_df = engine.compute_shift_stratification()

        assert len(shift_df) == 2
        shift_a = shift_df[shift_df["shift"] == "Shift A"].iloc[0]
        shift_c = shift_df[shift_df["shift"] == "Shift C"].iloc[0]

        assert shift_a["avg_cycle_min"] == 90.0
        assert shift_a["avg_waiting_min"] == 15.0
        assert shift_a["nva_waiting_pct"] == round((15.0 / 90.0) * 100, 1)

        assert shift_c["avg_cycle_min"] == 150.0
        assert shift_c["avg_waiting_min"] == 60.0
        assert shift_c["nva_waiting_pct"] == 40.0

    def test_agency_benchmark_performance(self, sample_analytics_dataframe):
        engine = LogisticsAnalyticsEngine(sample_analytics_dataframe)
        agency_df = engine.compute_agency_performance()

        assert len(agency_df) == 2
        guru = agency_df[agency_df["agency"] == "Guru"].iloc[0]
        sudha = agency_df[agency_df["agency"] == "Sudha"].iloc[0]

        # Guru: 35 min avg loading <= 45m benchmark
        assert guru["avg_loading_min"] == 35.0
        assert guru["compliance_pct"] == 100.0
        assert guru["status"] == "Benchmark Compliant"

        # Sudha: 55 min avg loading > 45m benchmark
        assert sudha["avg_loading_min"] == 55.0
        assert sudha["compliance_pct"] == 0.0
        assert sudha["status"] == "Severe Loading Bottleneck"

    def test_fleet_throughput_scalability(self, sample_analytics_dataframe):
        engine = LogisticsAnalyticsEngine(sample_analytics_dataframe)
        scalability = engine.compute_fleet_throughput_and_scalability()

        assert scalability["total_fleet_size"] == 20
        assert scalability["shift_c_excess_wait_min_per_trip"] == 45.0  # 60m - 15m
        assert scalability["potential_additional_trips_per_day"] > 0

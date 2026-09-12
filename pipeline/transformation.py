"""
Transformation and Time-Delta Engine for Logistics Operations.
Handles data cleaning, robust timestamp normalization, midnight rollover resolution,
and vectorized component duration calculations.
"""
import re
import logging
import hashlib
from datetime import datetime, date, time, timedelta
from typing import Optional, Union, Tuple
import numpy as np
import pandas as pd

logger = logging.getLogger("pipeline.transformation")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


class LogisticsDataTransformer:
    """
    Transforms raw extracted trip records into clean, validated, and normalized records
    with computed operational cycle metrics.
    """

    STANDARD_LOADING_BENCHMARK_MIN = 45.0

    def __init__(self):
        pass

    @staticmethod
    def _parse_date(val: any) -> Optional[date]:
        """
        Safely parse diverse date formats into a standard datetime.date object.
        """
        if pd.isna(val) or val is None or str(val).strip() == "":
            return None
        if isinstance(val, date) and not isinstance(val, datetime):
            return val
        if isinstance(val, datetime) or isinstance(val, pd.Timestamp):
            return val.date()

        val_str = str(val).strip()
        # Common date parsing patterns
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d.%m.%Y", "%Y%m%d"):
            try:
                return datetime.strptime(val_str, fmt).date()
            except ValueError:
                continue

        try:
            parsed = pd.to_datetime(val_str, errors="coerce")
            if pd.notna(parsed):
                return parsed.date()
        except Exception:
            pass

        return None

    @staticmethod
    def _parse_time(val: any) -> Optional[time]:
        """
        Safely parse diverse time formats (strings, Excel floats, datetime.time/datetime).
        """
        if pd.isna(val) or val is None or str(val).strip() == "":
            return None
        if isinstance(val, time):
            return val
        if isinstance(val, datetime) or isinstance(val, pd.Timestamp):
            return val.time()

        # Handle numeric float from Excel (e.g. 0.5 -> 12:00:00)
        if isinstance(val, (int, float)):
            try:
                # Excel fraction of a 24-hour day
                total_seconds = int(round(float(val) * 86400))
                hours = (total_seconds // 3600) % 24
                minutes = (total_seconds % 3600) // 60
                seconds = total_seconds % 60
                return time(hour=hours, minute=minutes, second=seconds)
            except Exception:
                pass

        val_str = str(val).strip()
        # Clean up stray periods or spaces (e.g. '18.30' -> '18:30')
        if re.match(r"^\d{1,2}\.\d{2}$", val_str):
            val_str = val_str.replace(".", ":")
        elif re.match(r"^\d{4}$", val_str):
            val_str = f"{val_str[:2]}:{val_str[2:]}"

        time_formats = (
            "%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p",
            "%I:%M:%S%p", "%I:%M%p", "%H.%M.%S", "%H.%M"
        )
        for fmt in time_formats:
            try:
                return datetime.strptime(val_str, fmt).time()
            except ValueError:
                continue

        # Try pandas parser fallback
        try:
            parsed = pd.to_datetime(val_str, errors="coerce")
            if pd.notna(parsed):
                return parsed.time()
        except Exception:
            pass

        return None

    @staticmethod
    def _standardize_shift(val: any, start_time: Optional[time] = None) -> str:
        """
        Standardize shift identifiers to 'Shift A', 'Shift B', or 'Shift C'.
        Infers shift from start time if missing.
        """
        if pd.notna(val) and str(val).strip() != "":
            clean = str(val).strip().upper()
            if "A" in clean or clean in ("1", "FIRST", "MORNING"):
                return "Shift A"
            if "B" in clean or clean in ("2", "SECOND", "AFTERNOON", "EVENING"):
                return "Shift B"
            if "C" in clean or clean in ("3", "THIRD", "NIGHT"):
                return "Shift C"

        # Infer based on start time if available
        # Shift A: 06:00 - 14:00
        # Shift B: 14:00 - 22:00
        # Shift C: 22:00 - 06:00
        if start_time:
            h = start_time.hour
            if 6 <= h < 14:
                return "Shift A"
            elif 14 <= h < 22:
                return "Shift B"
            else:
                return "Shift C"

        return "Shift A"

    @staticmethod
    def _standardize_agency(val: any) -> str:
        """
        Standardize agency contractor names with canonical casing and trimming.
        """
        if pd.isna(val) or str(val).strip() == "":
            return "Unknown"
        clean = str(val).strip()
        # Normalize common spelling variations
        aliases = {
            "guru": "Guru",
            "tharini": "Tharini",
            "tarini": "Tharini",
            "meta": "Meta",
            "sahu": "Sahu",
            "sudha": "Sudha",
            "shree": "Shree",
            "jsw_internal": "Internal Plant Fleet"
        }
        low = clean.lower()
        for key, canonical in aliases.items():
            if key in low:
                return canonical
        return clean.title()

    def _resolve_trip_timestamps(
        self,
        base_date: date,
        t_arr_agency: Optional[time],
        t_leave_agency: Optional[time],
        t_arr_wait: Optional[time],
        t_leave_wait: Optional[time],
        t_arr_yard: Optional[time],
        t_leave_yard: Optional[time],
        shift: str
    ) -> Tuple[
        Optional[datetime], Optional[datetime],
        Optional[datetime], Optional[datetime],
        Optional[datetime], Optional[datetime]
    ]:
        """
        Combines base date with time objects and resolves midnight rollovers across
        the sequential operational milestones.
        """
        if not base_date:
            base_date = date(2026, 1, 1)

        raw_times = [
            t_arr_agency,
            t_leave_agency,
            t_arr_wait,
            t_leave_wait,
            t_arr_yard,
            t_leave_yard
        ]

        # Shift C (22:00 to 06:00) nuance:
        # If the trip started in the early morning hours (00:00 to 06:00) on Shift C,
        # but the sheet recorded the date as the shift inception date (yesterday),
        # or vice versa.
        first_time = next((t for t in raw_times if t is not None), None)
        current_dt = datetime.combine(base_date, first_time) if first_time else None

        datetimes = []
        last_dt = None

        for t in raw_times:
            if t is None:
                datetimes.append(None)
                continue

            if last_dt is None:
                # First timestamp of trip
                candidate_dt = datetime.combine(base_date, t)
                # If Shift C started at 00:xx - 05:xx, treat as day+1 if base_date is shift-start evening
                if shift == "Shift C" and t.hour < 7:
                    # Trip started after midnight of Shift C
                    pass
                last_dt = candidate_dt
                datetimes.append(candidate_dt)
            else:
                # Construct candidate using same date as previous timestamp
                candidate_dt = datetime.combine(last_dt.date(), t)
                # If candidate is before last_dt (e.g. 23:45 -> 00:30), roll over to next calendar day
                if candidate_dt < last_dt:
                    candidate_dt += timedelta(days=1)
                
                # Check for excessive jump (> 18 hours), which could indicate bad format; cap or correct
                if (candidate_dt - last_dt).total_seconds() > 18 * 3600:
                    # Potential anomaly or format flip
                    pass

                last_dt = candidate_dt
                datetimes.append(candidate_dt)

        return tuple(datetimes)

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Execute end-to-end transformation, timestamp normalization, and duration computation.
        """
        if df.empty:
            logger.warning("Empty DataFrame passed to transform().")
            return pd.DataFrame()

        records = []
        logger.info(f"Starting transformation of {len(df)} raw records...")

        for idx, row in df.iterrows():
            # 1. Base identifiers
            sr_no = int(row["sr_no"]) if "sr_no" in row and pd.notna(row["sr_no"]) and str(row["sr_no"]).isdigit() else (idx + 1)
            trailer_no = str(row.get("trailer_no", f"TR-{idx+1000}")).strip().upper()
            if trailer_no in ("NAN", "NONE", ""):
                trailer_no = f"TR-{idx+1000}"

            raw_date = row.get("date", None)
            trip_date = self._parse_date(raw_date) if raw_date is not None else date(2026, 1, 1)
            if not trip_date:
                trip_date = date(2026, 1, 1)

            agency = self._standardize_agency(row.get("agency", "Unknown"))
            remarks = str(row.get("remarks", "")).strip() if pd.notna(row.get("remarks")) else None
            if remarks in ("nan", "None", ""):
                remarks = None

            # 2. Time components parsing
            t_arr_agency = self._parse_time(row.get("arriving_agency"))
            t_leave_agency = self._parse_time(row.get("leaving_agency"))
            t_arr_wait = self._parse_time(row.get("arriving_waiting"))
            t_leave_wait = self._parse_time(row.get("leaving_waiting"))
            t_arr_yard = self._parse_time(row.get("arriving_fg_yard"))
            t_leave_yard = self._parse_time(row.get("leaving_fg_yard"))

            # 3. Shift resolution
            shift = self._standardize_shift(row.get("shift"), start_time=t_arr_agency or t_leave_agency)

            # 4. Resolve full Datetimes & Midnight Rollovers
            (
                dt_arr_agency,
                dt_leave_agency,
                dt_arr_wait,
                dt_leave_wait,
                dt_arr_yard,
                dt_leave_yard
            ) = self._resolve_trip_timestamps(
                trip_date,
                t_arr_agency,
                t_leave_agency,
                t_arr_wait,
                t_leave_wait,
                t_arr_yard,
                t_leave_yard,
                shift
            )

            # 5. Compute Process-wise Component Durations (in Minutes)
            # A. Loading Time = Leaving Agency - Arriving Agency
            loading_min = None
            if dt_arr_agency and dt_leave_agency:
                loading_min = max(0.0, round((dt_leave_agency - dt_arr_agency).total_seconds() / 60.0, 2))

            # B. Waiting Time (NVA) = Leaving Waiting Area - Arriving Waiting Area
            waiting_min = 0.0
            if dt_arr_wait and dt_leave_wait:
                waiting_min = max(0.0, round((dt_leave_wait - dt_arr_wait).total_seconds() / 60.0, 2))

            # C. Unloading Time = Leaving FG Yard - Arriving FG Yard
            unloading_min = None
            if dt_arr_yard and dt_leave_yard:
                unloading_min = max(0.0, round((dt_leave_yard - dt_arr_yard).total_seconds() / 60.0, 2))

            # D. Total Cycle Time = Leaving FG Yard - Arriving Agency
            # Fallback to sum of components if outer timestamps are missing
            total_cycle_min = None
            if dt_arr_agency and dt_leave_yard:
                total_cycle_min = max(0.0, round((dt_leave_yard - dt_arr_agency).total_seconds() / 60.0, 2))
            elif dt_leave_agency and dt_arr_yard:
                # Approximate
                est_loading = loading_min or 40.0
                est_unloading = unloading_min or 30.0
                mid_delta = max(0.0, (dt_arr_yard - dt_leave_agency).total_seconds() / 60.0)
                total_cycle_min = round(est_loading + mid_delta + est_unloading, 2)

            # E. Transit Time = Inter-station travel
            transit_min = 0.0
            if total_cycle_min is not None:
                accounted = (loading_min or 0.0) + (waiting_min or 0.0) + (unloading_min or 0.0)
                transit_min = max(0.0, round(total_cycle_min - accounted, 2))

            # If total_cycle_min is still None, sum available components
            if total_cycle_min is None:
                total_cycle_min = round((loading_min or 0.0) + (waiting_min or 0.0) + (unloading_min or 0.0) + transit_min, 2)

            # 6. Operational Flags
            is_loading_delayed = bool(loading_min is not None and float(loading_min) > self.STANDARD_LOADING_BENCHMARK_MIN)
            is_shift_c_bottleneck = bool(
                shift == "Shift C" and (
                    (waiting_min is not None and float(waiting_min) >= 35.0) or
                    (total_cycle_min is not None and float(total_cycle_min) >= 130.0)
                )
            )

            # 7. Unique Trip Identifier for Deduplication
            date_str = trip_date.strftime("%Y%m%d")
            source_file = str(row.get("source_file", "unknown"))
            source_sheet = str(row.get("source_sheet", "unknown"))
            uid_raw = f"{source_file}_{source_sheet}_{date_str}_{trailer_no}_{sr_no}_{idx}"
            trip_uid = hashlib.md5(uid_raw.encode("utf-8")).hexdigest()[:16]

            records.append({
                "trip_uid": trip_uid,
                "sr_no": sr_no,
                "trailer_no": trailer_no,
                "trip_date": trip_date,
                "shift": shift,
                "agency": agency,
                "remarks": remarks,
                "arriving_agency": dt_arr_agency,
                "leaving_agency": dt_leave_agency,
                "arriving_waiting": dt_arr_wait,
                "leaving_waiting": dt_leave_wait,
                "arriving_fg_yard": dt_arr_yard,
                "leaving_fg_yard": dt_leave_yard,
                "loading_time_min": loading_min,
                "waiting_time_min": waiting_min,
                "unloading_time_min": unloading_min,
                "transit_time_min": transit_min,
                "total_cycle_time_min": total_cycle_min,
                "is_loading_delayed": is_loading_delayed,
                "is_shift_c_bottleneck": is_shift_c_bottleneck,
                "source_file": source_file,
                "source_sheet": source_sheet,
                "created_at": datetime.now()
            })

        transformed_df = pd.DataFrame(records)
        logger.info(
            f"Transformation completed. Successfully transformed {len(transformed_df)} records. "
            f"(Delayed loading: {transformed_df['is_loading_delayed'].sum()}, "
            f"Shift C bottlenecks: {transformed_df['is_shift_c_bottleneck'].sum()})"
        )
        return transformed_df

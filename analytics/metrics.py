"""
Operational Logistics Analytics & KPI Engine.
Computes Shift Stratification Bottlenecks, Agency Loading Benchmarks (vs 45-min target),
Hourly Congestion Waves, and Fleet Throughput Scalability.
"""
import logging
from typing import Dict, Any, Optional
import pandas as pd
import numpy as np

logger = logging.getLogger("analytics.metrics")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


class LogisticsAnalyticsEngine:
    """
    Core analytics calculation engine for plant logistics operations.
    """

    BENCHMARK_LOADING_MINUTES = 45.0

    def __init__(self, df: pd.DataFrame):
        """
        Initialize with a transformed trips DataFrame.
        """
        self.df = df.copy()
        if not self.df.empty:
            # Ensure proper datetime casting if needed
            for col in ["arriving_agency", "leaving_agency", "arriving_waiting", "leaving_waiting", "arriving_fg_yard", "leaving_fg_yard"]:
                if col in self.df.columns and not pd.api.types.is_datetime64_any_dtype(self.df[col]):
                    self.df[col] = pd.to_datetime(self.df[col], errors="coerce")

    def get_summary_overview(self) -> Dict[str, Any]:
        """
        High-level executive metrics.
        """
        if self.df.empty:
            return {
                "total_trips": 0,
                "avg_cycle_time_min": 0.0,
                "avg_loading_time_min": 0.0,
                "avg_waiting_time_min": 0.0,
                "avg_unloading_time_min": 0.0,
                "delayed_loading_trips": 0,
                "delayed_loading_pct": 0.0,
                "shift_c_bottleneck_trips": 0,
                "shift_c_bottleneck_pct": 0.0,
                "active_trailers": 0,
                "active_agencies": 0,
            }

        total_trips = len(self.df)
        avg_cycle = float(self.df["total_cycle_time_min"].mean(skipna=True) or 0.0)
        avg_loading = float(self.df["loading_time_min"].mean(skipna=True) or 0.0)
        avg_waiting = float(self.df["waiting_time_min"].mean(skipna=True) or 0.0)
        avg_unloading = float(self.df["unloading_time_min"].mean(skipna=True) or 0.0)
        
        delayed_trips = int(self.df["is_loading_delayed"].sum()) if "is_loading_delayed" in self.df.columns else 0
        delayed_pct = round((delayed_trips / total_trips) * 100, 1) if total_trips > 0 else 0.0

        bottlenecks = int(self.df["is_shift_c_bottleneck"].sum()) if "is_shift_c_bottleneck" in self.df.columns else 0
        bottleneck_pct = round((bottlenecks / total_trips) * 100, 1) if total_trips > 0 else 0.0

        active_trailers = int(self.df["trailer_no"].nunique()) if "trailer_no" in self.df.columns else 0
        active_agencies = int(self.df["agency"].nunique()) if "agency" in self.df.columns else 0

        # Calculate plant-wide NVA (Non-Value-Added) waiting proportion
        nva_pct = round((avg_waiting / avg_cycle * 100), 1) if avg_cycle > 0 else 0.0

        return {
            "total_trips": total_trips,
            "avg_cycle_time_min": round(avg_cycle, 1),
            "avg_loading_time_min": round(avg_loading, 1),
            "avg_waiting_time_min": round(avg_waiting, 1),
            "avg_unloading_time_min": round(avg_unloading, 1),
            "nva_waiting_pct": nva_pct,
            "delayed_loading_trips": delayed_trips,
            "delayed_loading_pct": delayed_pct,
            "shift_c_bottleneck_trips": bottlenecks,
            "shift_c_bottleneck_pct": bottleneck_pct,
            "active_trailers": active_trailers,
            "active_agencies": active_agencies
        }

    def compute_shift_stratification(self) -> pd.DataFrame:
        """
        Group trips by Shift (Shift A, Shift B, Shift C) to reveal operational variances,
        specifically detecting Shift C cycle-time and waiting-time spikes.
        """
        if self.df.empty or "shift" not in self.df.columns:
            return pd.DataFrame()

        # Group and aggregate
        grouped = self.df.groupby("shift").agg(
            trip_count=("trailer_no", "count"),
            avg_loading_min=("loading_time_min", "mean"),
            avg_waiting_min=("waiting_time_min", "mean"),
            avg_unloading_min=("unloading_time_min", "mean"),
            avg_transit_min=("transit_time_min", "mean"),
            avg_cycle_min=("total_cycle_time_min", "mean"),
            median_cycle_min=("total_cycle_time_min", "median"),
            std_cycle_min=("total_cycle_time_min", "std"),
            delayed_trips=("is_loading_delayed", "sum"),
            bottleneck_trips=("is_shift_c_bottleneck", "sum")
        ).reset_index()

        # Round all float columns
        for col in grouped.columns:
            if grouped[col].dtype == "float64":
                grouped[col] = grouped[col].round(1)

        # Calculate NVA % per shift
        grouped["nva_waiting_pct"] = (
            (grouped["avg_waiting_min"] / grouped["avg_cycle_min"]) * 100
        ).round(1)

        # Order by standard shift order: Shift A, Shift B, Shift C
        order_map = {"Shift A": 1, "Shift B": 2, "Shift C": 3}
        grouped["sort_order"] = grouped["shift"].map(order_map).fillna(99)
        grouped = grouped.sort_values("sort_order").drop(columns=["sort_order"])

        # Calculate Shift C vs Shift A bottleneck surge delta %
        shift_a = grouped[grouped["shift"] == "Shift A"]
        shift_c = grouped[grouped["shift"] == "Shift C"]
        if not shift_a.empty and not shift_c.empty:
            a_cycle = float(shift_a["avg_cycle_min"].iloc[0])
            c_cycle = float(shift_c["avg_cycle_min"].iloc[0])
            a_wait = float(shift_a["avg_waiting_min"].iloc[0])
            c_wait = float(shift_c["avg_waiting_min"].iloc[0])

            cycle_surge_pct = round(((c_cycle - a_cycle) / a_cycle) * 100, 1) if a_cycle > 0 else 0.0
            wait_surge_pct = round(((c_wait - a_wait) / a_wait) * 100, 1) if a_wait > 0 else 0.0
            
            logger.info(
                f"Shift Stratification computed: Shift C Total Cycle Time surge: +{cycle_surge_pct}% vs Shift A, "
                f"Waiting Time surge: +{wait_surge_pct}%"
            )

        return grouped

    def compute_agency_performance(self) -> pd.DataFrame:
        """
        Benchmark each agency's average loading time against the 45-minute standard threshold.
        """
        if self.df.empty or "agency" not in self.df.columns:
            return pd.DataFrame()

        def p90(x):
            return np.percentile(x.dropna(), 90) if len(x.dropna()) > 0 else np.nan

        grouped = self.df.groupby("agency").agg(
            trip_count=("trailer_no", "count"),
            avg_loading_min=("loading_time_min", "mean"),
            median_loading_min=("loading_time_min", "median"),
            p90_loading_min=("loading_time_min", p90),
            std_loading_min=("loading_time_min", "std"),
            avg_cycle_min=("total_cycle_time_min", "mean"),
            delayed_loading_count=("is_loading_delayed", "sum")
        ).reset_index()

        # Calculate variance from 45-min benchmark
        grouped["benchmark_variance_min"] = (
            grouped["avg_loading_min"] - self.BENCHMARK_LOADING_MINUTES
        ).round(1)

        # Compliance percentage
        grouped["compliance_pct"] = (
            ((grouped["trip_count"] - grouped["delayed_loading_count"]) / grouped["trip_count"]) * 100
        ).round(1)

        # Breach percentage
        grouped["breach_pct"] = (100.0 - grouped["compliance_pct"]).round(1)

        # Status categorization
        def categorize(row):
            if row["avg_loading_min"] <= self.BENCHMARK_LOADING_MINUTES and row["compliance_pct"] >= 80.0:
                return "Benchmark Compliant"
            elif row["avg_loading_min"] <= 50.0 and row["compliance_pct"] >= 50.0:
                return "Moderate Delay"
            else:
                return "Severe Loading Bottleneck"

        grouped["status"] = grouped.apply(categorize, axis=1)

        # Round floats
        for col in ["avg_loading_min", "median_loading_min", "p90_loading_min", "std_loading_min", "avg_cycle_min"]:
            grouped[col] = grouped[col].round(1)

        # Sort by best average loading time
        grouped = grouped.sort_values("avg_loading_min", ascending=True).reset_index(drop=True)
        return grouped

    def compute_hourly_traffic_distribution(self) -> pd.DataFrame:
        """
        Analyze hourly vehicle queue buildup across the 24-hour cycle.
        """
        if self.df.empty:
            return pd.DataFrame()

        # Extract arrival hour at agency
        df_valid = self.df[self.df["arriving_agency"].notna()].copy()
        if df_valid.empty:
            return pd.DataFrame()

        df_valid["arrival_hour"] = df_valid["arriving_agency"].dt.hour

        hourly = df_valid.groupby("arrival_hour").agg(
            trips_arrived=("trailer_no", "count"),
            avg_cycle_time_min=("total_cycle_time_min", "mean"),
            avg_waiting_time_min=("waiting_time_min", "mean")
        ).reset_index()

        # Ensure all 24 hours are represented
        all_hours = pd.DataFrame({"arrival_hour": list(range(24))})
        hourly = pd.merge(all_hours, hourly, on="arrival_hour", how="left").fillna(0)

        hourly["avg_cycle_time_min"] = hourly["avg_cycle_time_min"].round(1)
        hourly["avg_waiting_time_min"] = hourly["avg_waiting_time_min"].round(1)
        hourly["trips_arrived"] = hourly["trips_arrived"].astype(int)

        return hourly

    def compute_fleet_throughput_and_scalability(self) -> Dict[str, Any]:
        """
        Calculate active fleet throughput, daily turnover rate, and potential throughput gain
        if Shift C NVA waiting time is brought down to Shift A baseline.
        """
        if self.df.empty:
            return {}

        total_trips = len(self.df)
        num_trailers = self.df["trailer_no"].nunique()
        
        # Calculate distinct operating days
        num_days = 1
        if "trip_date" in self.df.columns:
            num_days = max(1, self.df["trip_date"].nunique())

        trips_per_day = round(total_trips / num_days, 1)
        trips_per_trailer_per_day = round(trips_per_day / num_trailers, 2) if num_trailers > 0 else 0.0

        # Mean cycle times
        shift_summary = self.compute_shift_stratification()
        shift_a = shift_summary[shift_summary["shift"] == "Shift A"]
        shift_c = shift_summary[shift_summary["shift"] == "Shift C"]

        a_wait = float(shift_a["avg_waiting_min"].iloc[0]) if not shift_a.empty else 15.0
        c_wait = float(shift_c["avg_waiting_min"].iloc[0]) if not shift_c.empty else 55.0
        c_trips = int(shift_c["trip_count"].iloc[0]) if not shift_c.empty else 0

        # Excess waiting minutes in Shift C
        excess_wait_per_trip = max(0.0, c_wait - a_wait)
        total_reclaimable_minutes_per_day = (excess_wait_per_trip * c_trips) / num_days

        avg_cycle_overall = float(self.df["total_cycle_time_min"].mean() or 100.0)
        potential_extra_trips_per_day = round(total_reclaimable_minutes_per_day / avg_cycle_overall, 1) if avg_cycle_overall > 0 else 0.0
        capacity_gain_pct = round((potential_extra_trips_per_day / trips_per_day) * 100, 1) if trips_per_day > 0 else 0.0

        return {
            "total_fleet_size": num_trailers,
            "operating_days": num_days,
            "total_trips_processed": total_trips,
            "avg_trips_per_day": trips_per_day,
            "turnover_trips_per_trailer_day": trips_per_trailer_per_day,
            "shift_c_excess_wait_min_per_trip": round(excess_wait_per_trip, 1),
            "reclaimable_hours_per_day": round(total_reclaimable_minutes_per_day / 60.0, 1),
            "potential_additional_trips_per_day": potential_extra_trips_per_day,
            "potential_capacity_expansion_pct": capacity_gain_pct
        }

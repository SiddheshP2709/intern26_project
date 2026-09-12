"""
Industrial Logistics & Operations Analytics Pipeline
Main Application Entrypoint: Supports both CLI Execution and Interactive Streamlit Dashboard.
"""
import os
import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from tabulate import tabulate

from pipeline import run_pipeline, DatabaseLoader
from analytics import LogisticsAnalyticsEngine

# Setup directories
BASE_DIR = Path(__file__).resolve().parent
OUTPUTS_DIR = BASE_DIR / "outputs"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = BASE_DIR / "data"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("app")


def generate_static_visualizations(df: pd.DataFrame, output_dir: Path = OUTPUTS_DIR):
    """
    Generate professional, publication-quality static charts and save to outputs directory.
    """
    if df.empty:
        logger.warning("No data available to generate static charts.")
        return []

    saved_files = []
    sns.set_theme(style="whitegrid")
    palette = sns.color_palette("deep")

    # 1. Shift Bottleneck & Cycle Time Breakdown
    plt.figure(figsize=(10, 6), dpi=300)
    shift_order = ["Shift A", "Shift B", "Shift C"]
    df_sorted_shift = df[df["shift"].isin(shift_order)].copy()
    
    # Subplot with Boxplots for Waiting Time vs Total Cycle Time
    fig, axes = plt.subplots(1, 2, figsize=(14, 6), dpi=300)
    
    sns.boxplot(
        data=df_sorted_shift, x="shift", y="total_cycle_time_min", hue="shift",
        order=shift_order, palette=["#2B6CB0", "#319795", "#C53030"], ax=axes[0], legend=False
    )
    axes[0].set_title("Total Turnaround Cycle Time by Shift", fontsize=13, fontweight="bold", pad=12)
    axes[0].set_xlabel("Operational Shift", fontsize=11)
    axes[0].set_ylabel("Duration (Minutes)", fontsize=11)
    
    sns.barplot(
        data=df_sorted_shift, x="shift", y="waiting_time_min", hue="shift",
        order=shift_order, palette=["#4299E1", "#4FD1C5", "#E53E3E"], ax=axes[1], errorbar=None, legend=False
    )
    axes[1].set_title("Mean Non-Value-Added (NVA) Waiting Time by Shift", fontsize=13, fontweight="bold", pad=12)
    axes[1].set_xlabel("Operational Shift", fontsize=11)
    axes[1].set_ylabel("Waiting Time (Minutes)", fontsize=11)
    
    # Annotate Shift C surge
    a_mean = df_sorted_shift[df_sorted_shift["shift"] == "Shift A"]["waiting_time_min"].mean()
    c_mean = df_sorted_shift[df_sorted_shift["shift"] == "Shift C"]["waiting_time_min"].mean()
    if a_mean and c_mean and a_mean > 0:
        surge = ((c_mean - a_mean) / a_mean) * 100
        axes[1].text(
            2, c_mean + 2, f"+{surge:.0f}% Surge\n(Bottleneck)",
            ha="center", color="#9B2C2C", fontweight="bold", fontsize=10
        )

    plt.tight_layout()
    shift_chart_path = output_dir / "shift_cycle_bottleneck.png"
    plt.savefig(shift_chart_path)
    plt.close()
    saved_files.append(shift_chart_path)

    # 2. Agency Loading Performance vs 45-min Benchmark
    plt.figure(figsize=(10, 6), dpi=300)
    agency_avg = df.groupby("agency")["loading_time_min"].mean().reset_index().sort_values("loading_time_min")
    
    colors = ["#38A169" if val <= 45.0 else "#E53E3E" for val in agency_avg["loading_time_min"]]
    bars = plt.barh(agency_avg["agency"], agency_avg["loading_time_min"], color=colors, height=0.55)
    plt.axvline(45.0, color="#DD6B20", linestyle="--", linewidth=2, label="45-Min Plant Benchmark")
    
    for bar in bars:
        width = bar.get_width()
        plt.text(width + 1.0, bar.get_y() + bar.get_height() / 2, f"{width:.1f}m", va="center", fontsize=10, fontweight="bold")

    plt.title("Transporter Agency Loading Time vs 45-Min Benchmark", fontsize=13, fontweight="bold", pad=15)
    plt.xlabel("Average Loading Duration (Minutes)", fontsize=11)
    plt.ylabel("Agency Contractor", fontsize=11)
    plt.xlim(0, max(agency_avg["loading_time_min"].max() + 12, 60))
    plt.legend(loc="lower right", frameon=True)
    plt.tight_layout()
    agency_chart_path = output_dir / "agency_loading_benchmarks.png"
    plt.savefig(agency_chart_path)
    plt.close()
    saved_files.append(agency_chart_path)

    # 3. Process Time Component Decomposition
    plt.figure(figsize=(10, 6), dpi=300)
    components = df.groupby("shift")[["loading_time_min", "waiting_time_min", "unloading_time_min", "transit_time_min"]].mean().reindex(shift_order)
    
    bottom_vals = np.zeros(len(components))
    comp_colors = {"loading_time_min": "#3182CE", "waiting_time_min": "#E53E3E", "transit_time_min": "#ECC94B", "unloading_time_min": "#38A169"}
    labels = {"loading_time_min": "Loading (Agency)", "waiting_time_min": "Waiting Area (NVA)", "transit_time_min": "Transit Travel", "unloading_time_min": "Unloading (FG Yard)"}

    for col in ["loading_time_min", "waiting_time_min", "transit_time_min", "unloading_time_min"]:
        vals = components[col].values
        plt.bar(components.index, vals, bottom=bottom_vals, label=labels[col], color=comp_colors[col], width=0.5)
        bottom_vals += vals

    plt.title("Process-wise Duration Breakdown Across Shifts (Minutes)", fontsize=13, fontweight="bold", pad=15)
    plt.ylabel("Cumulative Minutes", fontsize=11)
    plt.legend(loc="upper left", frameon=True)
    plt.tight_layout()
    decomp_chart_path = output_dir / "process_time_decomposition.png"
    plt.savefig(decomp_chart_path)
    plt.close()
    saved_files.append(decomp_chart_path)

    # 4. Hourly Congestion Distribution
    if "arriving_agency" in df.columns and df["arriving_agency"].notna().any():
        plt.figure(figsize=(12, 5), dpi=300)
        df_hr = df[df["arriving_agency"].notna()].copy()
        df_hr["hour"] = df_hr["arriving_agency"].dt.hour
        hourly_counts = df_hr.groupby("hour")["trailer_no"].count().reindex(range(24), fill_value=0)
        hourly_wait = df_hr.groupby("hour")["waiting_time_min"].mean().reindex(range(24), fill_value=0)

        fig, ax1 = plt.subplots(figsize=(12, 5), dpi=300)
        ax2 = ax1.twinx()

        ax1.bar(hourly_counts.index, hourly_counts.values, color="#3182CE", alpha=0.65, label="Trips Dispatched (Volume)")
        ax2.plot(hourly_wait.index, hourly_wait.values, color="#E53E3E", marker="o", linewidth=2.5, label="Avg Waiting Time (Minutes)")

        ax1.set_xlabel("Hour of Day (24-Hr Clock)", fontsize=11)
        ax1.set_ylabel("Trips Count", color="#3182CE", fontsize=11)
        ax2.set_ylabel("Avg Waiting Time (Mins)", color="#E53E3E", fontsize=11)
        ax1.set_xticks(range(0, 24, 2))
        ax1.set_title("Hourly Dispatch Volume vs Yard Waiting Congestion Curve", fontsize=13, fontweight="bold", pad=12)
        
        plt.tight_layout()
        hourly_chart_path = output_dir / "hourly_traffic_congestion.png"
        plt.savefig(hourly_chart_path)
        plt.close()
        saved_files.append(hourly_chart_path)

    logger.info(f"Generated {len(saved_files)} publication-grade charts in {output_dir}")
    return saved_files


def run_cli_mode():
    """
    Execute end-to-end pipeline in CLI mode, calculate KPIs, print formatted tables,
    and save static charts.
    """
    print("=" * 80)
    print(" INDUSTRIAL LOGISTICS & OPERATIONS ANALYTICS PIPELINE (CLI MODE)")
    print("=" * 80)

    # 1. Run Pipeline
    print("\n[STEP 1/4] Running ETL Ingestion & Database Loading...")
    result = run_pipeline()
    print(f" -> Files Ingested : {result.get('ingested_files', 0)}")
    print(f" -> Records Extracted: {result.get('total_extracted', 0)}")
    print(f" -> Database Loaded  : {result.get('records_loaded', 0)} inserted, {result.get('records_updated', 0)} updated")

    # 2. Fetch Data from SQLite
    print("\n[STEP 2/4] Querying Relational Storage (logistics.db)...")
    loader = DatabaseLoader()
    df = loader.fetch_trips_dataframe()

    if df.empty:
        print("[ERROR] Database contains no trips. Please check your /data/ directory.")
        return

    # 3. Compute Operational Analytics
    print("\n[STEP 3/4] Computing Operational KPIs & Stratification Models...")
    analytics = LogisticsAnalyticsEngine(df)

    summary = analytics.get_summary_overview()
    shift_df = analytics.compute_shift_stratification()
    agency_df = analytics.compute_agency_performance()
    fleet_scalability = analytics.compute_fleet_throughput_and_scalability()

    # Print Executive Summary Table
    print("\n" + "=" * 80)
    print(" EXECUTIVE LOGISTICS PERFORMANCE OVERVIEW")
    print("=" * 80)
    overview_table = [
        ["Total Outward Trips Analyzed", f"{summary['total_trips']} trips"],
        ["Active Trailer Fleet Size", f"{summary['active_trailers']} unique trailers"],
        ["Average Turnaround Cycle Time", f"{summary['avg_cycle_time_min']} mins"],
        ["Average Loading Time (Agency)", f"{summary['avg_loading_time_min']} mins (Benchmark: 45.0m)"],
        ["Average Waiting Time (NVA)", f"{summary['avg_waiting_time_min']} mins ({summary['nva_waiting_pct']}% of cycle)"],
        ["Average Unloading Time (FG Yard)", f"{summary['avg_unloading_time_min']} mins"],
        ["Loading Benchmark Breaches (>45m)", f"{summary['delayed_loading_trips']} trips ({summary['delayed_loading_pct']}%)"],
        ["Shift C Bottleneck Severe Delays", f"{summary['shift_c_bottleneck_trips']} trips ({summary['shift_c_bottleneck_pct']}%)"]
    ]
    print(tabulate(overview_table, headers=["Operational KPI", "Metric Value"], tablefmt="grid"))

    # Print Shift Bottleneck Table
    print("\n" + "=" * 80)
    print(" SHIFT BOTTLENECK STRATIFICATION")
    print("=" * 80)
    shift_display = shift_df[[
        "shift", "trip_count", "avg_loading_min", "avg_waiting_min", "avg_unloading_min",
        "avg_cycle_min", "nva_waiting_pct", "delayed_trips"
    ]].rename(columns={
        "shift": "Shift", "trip_count": "Trips", "avg_loading_min": "Load (m)",
        "avg_waiting_min": "Wait/NVA (m)", "avg_unloading_min": "Unload (m)",
        "avg_cycle_min": "Total Cycle (m)", "nva_waiting_pct": "NVA %", "delayed_trips": "Delays"
    })
    print(tabulate(shift_display, headers="keys", tablefmt="grid", showindex=False))

    # Print Agency Benchmarking Table
    print("\n" + "=" * 80)
    print(" TRANSPORTER AGENCY LOADING PERFORMANCE vs 45-MIN BENCHMARK")
    print("=" * 80)
    agency_display = agency_df[[
        "agency", "trip_count", "avg_loading_min", "median_loading_min", "p90_loading_min",
        "compliance_pct", "benchmark_variance_min", "status"
    ]].rename(columns={
        "agency": "Agency", "trip_count": "Trips", "avg_loading_min": "Avg Load (m)",
        "median_loading_min": "Median (m)", "p90_loading_min": "P90 (m)",
        "compliance_pct": "Compliance %", "benchmark_variance_min": "Var vs 45m", "status": "Performance Category"
    })
    print(tabulate(agency_display, headers="keys", tablefmt="grid", showindex=False))

    # Print Throughput & Capacity Table
    print("\n" + "=" * 80)
    print(" FLEET THROUGHPUT & BOTTLENECK RECLAMATION POTENTIAL")
    print("=" * 80)
    fleet_table = [
        ["Total Fleet Size", f"{fleet_scalability.get('total_fleet_size')} trailers"],
        ["Operating Days Analyzed", f"{fleet_scalability.get('operating_days')} days"],
        ["Current Average Trips / Day", f"{fleet_scalability.get('avg_trips_per_day')} trips/day"],
        ["Daily Fleet Turnover Ratio", f"{fleet_scalability.get('turnover_trips_per_trailer_day')} trips/trailer/day"],
        ["Shift C Excess Wait per Trip", f"{fleet_scalability.get('shift_c_excess_wait_min_per_trip')} mins/trip"],
        ["Reclaimable Lost Time / Day", f"{fleet_scalability.get('reclaimable_hours_per_day')} hours/day"],
        ["Potential Additional Capacity", f"+{fleet_scalability.get('potential_additional_trips_per_day')} extra trips/day (+{fleet_scalability.get('potential_capacity_expansion_pct')}%)"]
    ]
    print(tabulate(fleet_table, headers=["Throughput Metric", "Value"], tablefmt="grid"))

    # 4. Generate Visualizations
    print("\n[STEP 4/4] Exporting High-Resolution Visualizations to /outputs/...")
    saved_charts = generate_static_visualizations(df)
    for c in saved_charts:
        print(f" -> Exported: {c.relative_to(BASE_DIR)}")

    print("\n" + "=" * 80)
    print(" PIPELINE EXECUTION COMPLETED SUCCESSFULLY")
    print("=" * 80)


def run_streamlit_dashboard():
    """
    Launch interactive Streamlit dashboard.
    """
    import streamlit as st
    import plotly.express as px
    import plotly.graph_objects as go

    st.set_page_config(
        page_title="Industrial Logistics Analytics | JSW Steel BLM",
        page_icon="🚚",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # Custom styling
    st.markdown("""
        <style>
        .main-header {
            font-size: 2.2rem;
            font-weight: 700;
            color: #1A365D;
            margin-bottom: 0px;
        }
        .sub-header {
            font-size: 1.05rem;
            color: #4A5568;
            margin-bottom: 20px;
        }
        .metric-card {
            background-color: #F7FAFC;
            border-radius: 8px;
            padding: 16px;
            border-left: 4px solid #3182CE;
        }
        </style>
    """, unsafe_allow_html=True)

    # Title & Subheading
    st.markdown("<div class='main-header'>🏭 Industrial Logistics & Operations Analytics Pipeline</div>", unsafe_allow_html=True)
    st.markdown("<div class='sub-header'>Outward Trailer Tracking & Cycle Time Optimization | JSW BLM Finishing Lines to FG Yards</div>", unsafe_allow_html=True)

    # Sidebar
    st.sidebar.title("🛠️ Operations Control")
    st.sidebar.markdown("---")

    # ETL Trigger in Sidebar
    if st.sidebar.button("🔄 Re-run ETL Pipeline Ingestion", type="primary", use_container_width=True):
        with st.spinner("Ingesting raw Excel workbooks, computing deltas, and loading SQLite..."):
            res = run_pipeline()
            st.sidebar.success(f"ETL Complete: {res.get('records_loaded', 0)} loaded / {res.get('records_updated', 0)} updated.")

    # Load data from database
    loader = DatabaseLoader()
    df_all = loader.fetch_trips_dataframe()

    if df_all.empty:
        st.warning("No records found in database. Please run ETL ingestion.")
        if st.button("Run ETL Ingestion Now"):
            run_pipeline()
            st.rerun()
        return

    # Sidebar Filters
    st.sidebar.markdown("### 🔍 Filter Parameters")
    
    # Date filter
    if "trip_date" in df_all.columns:
        dates = sorted(df_all["trip_date"].unique())
        selected_dates = st.sidebar.multiselect("Select Dates", options=dates, default=dates)
    else:
        selected_dates = []

    # Shift filter
    shifts = sorted(df_all["shift"].unique())
    selected_shifts = st.sidebar.multiselect("Select Shifts", options=shifts, default=shifts)

    # Agency filter
    agencies = sorted(df_all["agency"].unique())
    selected_agencies = st.sidebar.multiselect("Select Transporter Agencies", options=agencies, default=agencies)

    # Filter dataframe
    df_filtered = df_all.copy()
    if selected_dates:
        df_filtered = df_filtered[df_filtered["trip_date"].isin(selected_dates)]
    if selected_shifts:
        df_filtered = df_filtered[df_filtered["shift"].isin(selected_shifts)]
    if selected_agencies:
        df_filtered = df_filtered[df_filtered["agency"].isin(selected_agencies)]

    if df_filtered.empty:
        st.error("No records match the current filter selection.")
        return

    # Compute Analytics Engine
    analytics = LogisticsAnalyticsEngine(df_filtered)
    summary = analytics.get_summary_overview()
    shift_df = analytics.compute_shift_stratification()
    agency_df = analytics.compute_agency_performance()
    fleet_scalability = analytics.compute_fleet_throughput_and_scalability()

    # --- TOP KPI METRIC CARDS ---
    st.markdown("### 📊 Operational Summary KPI Cards")
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        st.metric("Total Trips Analyzed", f"{summary['total_trips']:,}", f"{summary['active_trailers']} trailers")
    with col2:
        st.metric("Avg Turnaround Cycle", f"{summary['avg_cycle_time_min']:.1f} m", "Full plant cycle")
    with col3:
        st.metric("Avg Loading Time", f"{summary['avg_loading_time_min']:.1f} m", f"{summary['delayed_loading_pct']}% > 45m benchmark", delta_color="inverse")
    with col4:
        st.metric("NVA Waiting Time", f"{summary['avg_waiting_time_min']:.1f} m", f"{summary['nva_waiting_pct']}% of total time", delta_color="inverse")
    with col5:
        st.metric("Shift C Bottleneck Surge", f"{summary['shift_c_bottleneck_pct']}%", "Severe night delays", delta_color="inverse")

    st.markdown("---")

    # --- TABS FOR ORGANIZED EXPLORATION ---
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🚨 Shift Bottleneck Stratification",
        "🏢 Agency 45-Min Benchmark",
        "⏱️ Process Decomposition",
        "📈 Fleet Scalability & Congestion",
        "🗃️ Trip Records Explorer"
    ])

    # --- TAB 1: SHIFT BOTTLENECK ---
    with tab1:
        st.markdown("#### 🚨 Shift Stratification & Cycle-Time Variance")
        st.info("Analysis of operational cycle times across shifts reveals severe bottlenecking during **Shift C (Night Shift)** due to yard queue accumulation and staffing imbalances.")

        col_left, col_right = st.columns([3, 2])
        with col_left:
            fig_shift = px.box(
                df_filtered,
                x="shift",
                y="total_cycle_time_min",
                color="shift",
                color_discrete_map={"Shift A": "#2B6CB0", "Shift B": "#319795", "Shift C": "#E53E3E"},
                title="Cycle Time Distribution by Shift (Minutes)",
                points="all"
            )
            fig_shift.update_layout(showlegend=False, xaxis_title="Operational Shift", yaxis_title="Total Cycle Duration (Minutes)")
            st.plotly_chart(fig_shift, use_container_width=True)

        with col_right:
            if not shift_df.empty:
                fig_wait_bar = px.bar(
                    shift_df,
                    x="shift",
                    y="avg_waiting_min",
                    color="shift",
                    color_discrete_map={"Shift A": "#4299E1", "Shift B": "#4FD1C5", "Shift C": "#C53030"},
                    title="Average Non-Value-Added (NVA) Waiting Time by Shift",
                    text_auto=".1f"
                )
                fig_wait_bar.update_layout(showlegend=False, xaxis_title="Shift", yaxis_title="Mean Waiting Time (Minutes)")
                st.plotly_chart(fig_wait_bar, use_container_width=True)

        # Shift Metric Table
        st.markdown("##### Detailed Shift Performance Matrix")
        st.dataframe(shift_df, use_container_width=True)

    # --- TAB 2: AGENCY BENCHMARKS ---
    with tab2:
        st.markdown("#### 🏢 Transporter Agency Loading Performance vs 45-Minute Standard")
        st.caption("Plant benchmark allows **45.0 minutes** for coil strapping and loading. Transporters exceeding this trigger operational demurrage and staging congestion.")

        col_a1, col_a2 = st.columns([3, 2])
        with col_a1:
            fig_agency = go.Figure()
            colors = ["#38A169" if row["avg_loading_min"] <= 45.0 else "#E53E3E" for _, row in agency_df.iterrows()]
            
            fig_agency.add_trace(go.Bar(
                x=agency_df["agency"],
                y=agency_df["avg_loading_min"],
                marker_color=colors,
                text=[f"{v:.1f}m" for v in agency_df["avg_loading_min"]],
                textposition="auto",
                name="Avg Loading Time"
            ))
            fig_agency.add_hline(y=45.0, line_dash="dash", line_color="#DD6B20", annotation_text="45-min Plant Standard", annotation_position="top left")
            fig_agency.update_layout(title="Average Loading Time per Transporter Agency", xaxis_title="Agency", yaxis_title="Minutes")
            st.plotly_chart(fig_agency, use_container_width=True)

        with col_a2:
            fig_comp = px.pie(
                agency_df,
                values="trip_count",
                names="status",
                title="Trips by Performance Compliance Category",
                color="status",
                color_discrete_map={
                    "Benchmark Compliant": "#38A169",
                    "Moderate Delay": "#D69E2E",
                    "Severe Loading Bottleneck": "#E53E3E"
                }
            )
            st.plotly_chart(fig_comp, use_container_width=True)

        st.markdown("##### Agency Compliance Scorecard")
        st.dataframe(agency_df, use_container_width=True)

    # --- TAB 3: PROCESS DECOMPOSITION ---
    with tab3:
        st.markdown("#### ⏱️ Process-Wise Turnaround Decomposition")
        st.caption("Breakdown of total cycle duration into **Loading (Agency)**, **Waiting Area (NVA Queue)**, **Transit Travel**, and **Unloading (FG Yard)**.")

        if not shift_df.empty:
            decomp_data = shift_df.melt(
                id_vars=["shift"],
                value_vars=["avg_loading_min", "avg_waiting_min", "avg_transit_min", "avg_unloading_min"],
                var_name="process_stage",
                value_name="duration_minutes"
            )
            stage_map = {
                "avg_loading_min": "1. Loading at Agency",
                "avg_waiting_min": "2. Waiting Area (NVA Queue)",
                "avg_transit_min": "3. Internal Transit",
                "avg_unloading_min": "4. Unloading at FG Yard"
            }
            decomp_data["process_stage"] = decomp_data["process_stage"].map(stage_map)

            fig_stack = px.bar(
                decomp_data,
                x="shift",
                y="duration_minutes",
                color="process_stage",
                title="Stacked Operational Stage Durations by Shift (Minutes)",
                color_discrete_sequence=["#3182CE", "#E53E3E", "#ECC94B", "#38A169"],
                barmode="stack",
                text_auto=".1f"
            )
            fig_stack.update_layout(xaxis_title="Shift", yaxis_title="Cumulative Duration (Minutes)")
            st.plotly_chart(fig_stack, use_container_width=True)

    # --- TAB 4: FLEET SCALABILITY & CONGESTION ---
    with tab4:
        st.markdown("#### 📈 Fleet Scalability & Yard Congestion Heatmap")
        
        col_c1, col_c2 = st.columns([3, 2])
        with col_c1:
            hourly_df = analytics.compute_hourly_traffic_distribution()
            if not hourly_df.empty:
                fig_hr = go.Figure()
                fig_hr.add_trace(go.Bar(
                    x=hourly_df["arrival_hour"],
                    y=hourly_df["trips_arrived"],
                    name="Arrivals (Trips)",
                    marker_color="#4299E1",
                    opacity=0.7
                ))
                fig_hr.add_trace(go.Scatter(
                    x=hourly_df["arrival_hour"],
                    y=hourly_df["avg_waiting_time_min"],
                    name="Avg Waiting Time (m)",
                    yaxis="y2",
                    mode="lines+markers",
                    line=dict(color="#E53E3E", width=3)
                ))
                fig_hr.update_layout(
                    title="24-Hour Dispatch Volume vs Waiting Area Queue Buildup",
                    xaxis=dict(title="Hour of Day", tickmode="linear", tick0=0, dtick=2),
                    yaxis=dict(title="Dispatch Trips Volume"),
                    yaxis2=dict(title="Avg Waiting Minutes", overlaying="y", side="right"),
                    legend=dict(x=0.01, y=0.99)
                )
                st.plotly_chart(fig_hr, use_container_width=True)

        with col_c2:
            st.markdown("##### 🚀 Throughput Reclamation Simulator")
            st.write(
                f"**Current Fleet Performance:**\n"
                f"- **{fleet_scalability.get('avg_trips_per_day')} trips/day** across {fleet_scalability.get('total_fleet_size')} trailers.\n"
                f"- **{fleet_scalability.get('shift_c_excess_wait_min_per_trip')} mins** excess waiting per trip in Shift C.\n"
                f"- **{fleet_scalability.get('reclaimable_hours_per_day')} hours/day** wasted in non-value-added queues."
            )
            reclaim_pct = st.slider("Simulated NVA Waiting Reduction (%)", 0, 100, 75, 5)
            extra_trips = round((fleet_scalability.get("potential_additional_trips_per_day", 0.0) * (reclaim_pct / 100.0)), 1)
            capacity_gain = round((fleet_scalability.get("potential_capacity_expansion_pct", 0.0) * (reclaim_pct / 100.0)), 1)

            st.success(
                f"🎯 **Simulation Outcome:**\n\n"
                f"Reducing Shift C waiting by **{reclaim_pct}%** unlocks **+{extra_trips} extra trips/day** "
                f"(**+{capacity_gain}% fleet capacity expansion**) with zero capital investment in new trailers!"
            )

    # --- TAB 5: TRIP EXPLORER ---
    with tab5:
        st.markdown("#### 🗃️ Relational Trip Records Explorer")
        st.caption("Search, inspect, and export cleaned trip records stored in `logistics.db`.")
        
        search_query = st.text_input("🔍 Quick Search by Trailer No, Agency, or Remarks:")
        display_df = df_filtered.copy()
        if search_query:
            mask = (
                display_df["trailer_no"].astype(str).str.contains(search_query, case=False, na=False) |
                display_df["agency"].astype(str).str.contains(search_query, case=False, na=False) |
                display_df["remarks"].astype(str).str.contains(search_query, case=False, na=False)
            )
            display_df = display_df[mask]

        st.dataframe(display_df, use_container_width=True)
        
        # CSV Export
        csv_data = display_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Download Filtered Data as CSV",
            data=csv_data,
            file_name=f"jsw_logistics_trips_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv"
        )


def main():
    parser = argparse.ArgumentParser(description="Industrial Logistics & Operations Analytics Pipeline")
    parser.add_argument("--cli", action="store_true", help="Run in CLI mode to process data, compute KPIs, and export charts")
    parser.add_argument("--reingest", action="store_true", help="Force re-ingestion of data directory")
    args = parser.parse_args()

    # Determine execution context
    is_streamlit = "streamlit" in sys.modules and any("run" in arg for arg in sys.argv)

    if args.cli:
        run_cli_mode()
    elif is_streamlit:
        run_streamlit_dashboard()
    else:
        # Default behavior: run CLI mode if executed via standard python app.py
        print("[INFO] Defaulting to CLI execution mode. (Use 'streamlit run app.py' to launch interactive UI)")
        run_cli_mode()


if __name__ == "__main__":
    main()

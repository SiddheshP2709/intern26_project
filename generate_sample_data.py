"""
Synthetic Data Generator for JSW Steel BLM Outward Logistics Tracking.
Creates realistic multi-sheet Excel tracking workbooks simulating steel plant
trailer dispatches from finishing lines to Finished Goods (FG) yards.
"""
import os
import random
from datetime import datetime, date, time, timedelta
from pathlib import Path
import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SAMPLE_FILE = DATA_DIR / "JSW_BLM_trips_data.xlsx"

AGENCIES = ["Guru", "Tharini", "Meta", "Sahu", "Sudha"]
AGENCY_WEIGHTS = [0.28, 0.24, 0.20, 0.16, 0.12]

# Agency loading characteristics (mean, std dev in minutes)
AGENCY_PROFILES = {
    "Guru": (36.0, 5.0),       # High performer
    "Tharini": (39.0, 6.0),    # Compliant
    "Meta": (44.0, 7.0),       # On border
    "Sahu": (52.0, 9.0),       # Slower, frequent coil delays
    "Sudha": (58.0, 11.0),     # Critical loading bottleneck
}

TRAILER_PREFIXES = ["KA-34-A", "KA-35-B", "MH-12-Q", "AP-04-TT", "OD-02-C", "TS-08-UA"]
REMARKS_POOL = [
    "Normal dispatch", "Direct bay clearance", "Standard strapping", "Coil bundling delay",
    "Crane queue at finishing line", "Shift handover delay", "Yard slot congestion",
    "Security gate check", "Scale calibration", "Tally sheet verification", "Bay 3 coil loading"
]


def generate_trip_sequence(trip_date: date, shift: str, agency: str, sr_no: int, trailer_no: str):
    """
    Generate realistic chronological timestamps for a single trip.
    """
    # 1. Base arrival time at agency based on Shift
    if shift == "Shift A":
        # 06:00 to 13:30
        start_hour = random.randint(6, 13)
        start_min = random.randint(0, 55)
        dt_arr_agency = datetime.combine(trip_date, time(start_hour, start_min))
        waiting_mean, waiting_std = 14.0, 4.0
    elif shift == "Shift B":
        # 14:00 to 21:30
        start_hour = random.randint(14, 21)
        start_min = random.randint(0, 55)
        dt_arr_agency = datetime.combine(trip_date, time(start_hour, start_min))
        waiting_mean, waiting_std = 22.0, 6.0
    else:  # Shift C (Night Shift with severe bottleneck)
        # 22:00 to 05:30 (crosses midnight)
        start_hour = random.choice([22, 23, 0, 1, 2, 3, 4])
        start_min = random.randint(0, 55)
        day_offset = 1 if start_hour < 6 else 0
        dt_arr_agency = datetime.combine(trip_date + timedelta(days=day_offset), time(start_hour, start_min))
        # Severe shift C waiting time surge: 55-80 mins!
        waiting_mean, waiting_std = 62.0, 14.0

    # 2. Loading Time at Agency (Agency Profile)
    load_mean, load_std = AGENCY_PROFILES.get(agency, (45.0, 8.0))
    loading_mins = max(20.0, random.gauss(load_mean, load_std))
    dt_leave_agency = dt_arr_agency + timedelta(minutes=loading_mins)

    # 3. Transit to Waiting Area or direct to yard (15% direct clearance)
    is_direct = (random.random() < 0.15 and shift != "Shift C")
    transit_1_mins = max(5.0, random.gauss(8.0, 2.0))
    dt_arr_waiting = dt_leave_agency + timedelta(minutes=transit_1_mins) if not is_direct else None

    # 4. Waiting Area NVA Duration
    if not is_direct:
        waiting_mins = max(5.0, random.gauss(waiting_mean, waiting_std))
        dt_leave_waiting = dt_arr_waiting + timedelta(minutes=waiting_mins)
        transit_2_mins = max(5.0, random.gauss(7.0, 2.0))
        dt_arr_yard = dt_leave_waiting + timedelta(minutes=transit_2_mins)
    else:
        dt_leave_waiting = None
        dt_arr_yard = dt_leave_agency + timedelta(minutes=transit_1_mins + 4.0)

    # 5. Unloading at FG Yard
    unload_mins = max(15.0, random.gauss(26.0, 5.0) if shift == "Shift A" else random.gauss(32.0, 7.0))
    dt_leave_yard = dt_arr_yard + timedelta(minutes=unload_mins)

    # Total elapsed
    total_mins = (dt_leave_yard - dt_arr_agency).total_seconds() / 60.0
    hrs = int(total_mins // 60)
    mins = int(total_mins % 60)
    total_time_str = f"{hrs}:{mins:02d}"

    # Remarks
    if loading_mins > 50:
        remarks = random.choice(["Coil bundling delay", "Crane queue at finishing line", "Bay 3 coil loading"])
    elif shift == "Shift C" and not is_direct and waiting_mins > 50:
        remarks = random.choice(["Yard slot congestion", "Shift handover delay", "Crane unavailable at FG bay"])
    else:
        remarks = random.choice(REMARKS_POOL)

    return {
        "Sr no.": sr_no,
        "Trailer No.": trailer_no,
        "Date": trip_date.strftime("%d-%m-%Y"),
        "Agency": agency,
        "Arriving at Agency": dt_arr_agency.strftime("%H:%M"),
        "Leaving the agency": dt_leave_agency.strftime("%H:%M"),
        "Arriving at Waiting area": dt_arr_waiting.strftime("%H:%M") if dt_arr_waiting else "",
        "Leaving the waiting area": dt_leave_waiting.strftime("%H:%M") if dt_leave_waiting else "",
        "Arriving at FG Yard": dt_arr_yard.strftime("%H:%M"),
        "Leaving the FG yard": dt_leave_yard.strftime("%H:%M"),
        "Remarks": remarks,
        "Shift": shift,
        "Total Time": total_time_str
    }


def create_workbook():
    """
    Generate Excel workbook with multiple daily operational sheets.
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # Remove default sheet

    # Generate 5 days of realistic multi-shift data
    base_date = date(2026, 3, 1)
    trailers = [f"{random.choice(TRAILER_PREFIXES)}-{random.randint(1000, 9999)}" for _ in range(35)]

    total_records_generated = 0

    for day_idx in range(5):
        current_date = base_date + timedelta(days=day_idx)
        sheet_title = f"{current_date.strftime('%Y-%m-%d')}_Dispatch"
        ws = wb.create_sheet(title=sheet_title)

        # Style definitions
        title_font = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
        title_fill = PatternFill(start_color="1A365D", end_color="1A365D", fill_type="solid")
        
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="2B6CB0", end_color="2B6CB0", fill_type="solid")

        thin_border = Border(
            left=Side(style="thin", color="CCCCCC"),
            right=Side(style="thin", color="CCCCCC"),
            top=Side(style="thin", color="CCCCCC"),
            bottom=Side(style="thin", color="CCCCCC")
        )

        # 1. Company Banner Header Block (Header Noise to test ingestion engine)
        ws.merge_cells("A1:M1")
        ws["A1"] = "JSW STEEL COATED PRODUCTS LTD - BLM LOGISTICS TRACKING SYSTEM"
        ws["A1"].font = title_font
        ws["A1"].fill = title_fill
        ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 28

        ws.merge_cells("A2:M2")
        ws["A2"] = f"Daily Outward Movement Log | Mill to FG Yard | Operational Date: {current_date.strftime('%d-%b-%Y')}"
        ws["A2"].font = Font(name="Calibri", size=10, italic=True, color="4A5568")
        ws["A2"].alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[2].height = 20

        # 2. Table Column Headers at Row 4 (Row 3 is empty noise)
        headers = [
            "Sr no.", "Trailer No.", "Date", "Agency",
            "Arriving at Agency", "Leaving the agency",
            "Arriving at Waiting area", "Leaving the waiting area",
            "Arriving at FG Yard", "Leaving the FG yard",
            "Remarks", "Shift", "Total Time"
        ]

        for col_num, h_text in enumerate(headers, 1):
            cell = ws.cell(row=4, column=col_num)
            cell.value = h_text
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border
        ws.row_dimensions[4].height = 24

        # 3. Generate 60-80 trips across shifts for the day
        row_idx = 5
        sr_counter = 1
        
        # Distribute across shifts: Shift A (40%), Shift B (35%), Shift C (25%)
        shift_distribution = (
            [("Shift A", 28), ("Shift B", 24), ("Shift C", 20)]
        )

        for shift_name, count in shift_distribution:
            for _ in range(count):
                agency = random.choices(AGENCIES, weights=AGENCY_WEIGHTS)[0]
                trailer = random.choice(trailers)
                trip_data = generate_trip_sequence(current_date, shift_name, agency, sr_counter, trailer)

                for c_idx, h_key in enumerate(headers, 1):
                    c = ws.cell(row=row_idx, column=c_idx)
                    c.value = trip_data[h_key]
                    c.border = thin_border
                    c.alignment = Alignment(horizontal="center" if c_idx not in (2, 4, 11) else "left", vertical="center")

                row_idx += 1
                sr_counter += 1
                total_records_generated += 1

        # 4. Summary footer at bottom (Noise test)
        ws.cell(row=row_idx + 1, column=2, value="Daily Dispatch Count Total:")
        ws.cell(row=row_idx + 1, column=2).font = Font(bold=True)
        ws.cell(row=row_idx + 1, column=3, value=sr_counter - 1)
        ws.cell(row=row_idx + 1, column=3).font = Font(bold=True)

        # Auto-fit columns
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    wb.save(SAMPLE_FILE)
    print(f"Generated realistic JSW logistics sample workbook at: {SAMPLE_FILE}")
    print(f"Total trips created: {total_records_generated} across 5 operational sheets.")


if __name__ == "__main__":
    create_workbook()

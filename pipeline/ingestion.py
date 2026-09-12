"""
Multi-sheet Excel Ingestion Engine for Industrial Outward Logistics Tracking.
"""
import os
import re
import logging
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import pandas as pd
import openpyxl

logger = logging.getLogger("pipeline.ingestion")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


class ExcelIngestionEngine:
    """
    Ingests raw Excel workbooks from the /data/ directory, handling multi-sheet layouts,
    variable header offsets, summary footers, and schema variations.
    """

    # Target canonical column names
    CANONICAL_COLUMNS = [
        "sr_no",
        "trailer_no",
        "date",
        "agency",
        "arriving_agency",
        "leaving_agency",
        "arriving_waiting",
        "leaving_waiting",
        "arriving_fg_yard",
        "leaving_fg_yard",
        "remarks",
        "shift",
        "total_time_raw"
    ]

    # Ordered dictionary of regex matching patterns to resolve naming discrepancies in Excel files
    # Note: Complex/compound phrases must appear before single-word matches
    COLUMN_MAPPINGS = {
        "sr_no": [r"^sr\.?\s*no\.?$", r"^s\.?\s*no\.?$", r"^serial\s*no\.?$", r"^sl\s*no\.?$", r"^sr$"],
        "trailer_no": [r"^trailer\s*no\.?$", r"^vehicle\s*no\.?$", r"^truck\s*no\.?$", r"^trailer$", r"^vehicle$"],
        "arriving_agency": [
            r"^(arr|arriving|entry|in).*(agency|mill|plant|finishing).*$",
            r"^(agency|mill|plant|finishing).*(in|arrival|entry).*$",
            r"^arr.*agency$"
        ],
        "leaving_agency": [
            r"^(leav|leaving|exit|out|dept|departure).*(agency|mill|plant|finishing).*$",
            r"^(agency|mill|plant|finishing).*(out|exit|dept|departure).*$",
            r"^leav.*agency$"
        ],
        "arriving_waiting": [
            r"^(arr|arriving|entry|in).*(wait|waiting|queue|staging).*$",
            r"^(wait|waiting|queue|staging).*(in|arrival|entry).*$",
            r"^arr.*wait.*$"
        ],
        "leaving_waiting": [
            r"^(leav|leaving|exit|out|dept|departure).*(wait|waiting|queue|staging).*$",
            r"^(wait|waiting|queue|staging).*(out|exit|dept|departure).*$",
            r"^leav.*wait.*$"
        ],
        "arriving_fg_yard": [
            r"^(arr|arriving|entry|in).*(fg|yard|dispatch|bay).*$",
            r"^(fg|yard|dispatch|bay).*(in|arrival|entry).*$",
            r"^arr.*(fg|yard).*$"
        ],
        "leaving_fg_yard": [
            r"^(leav|leaving|exit|out|dept|departure).*(fg|yard|dispatch|bay).*$",
            r"^(fg|yard|dispatch|bay).*(out|exit|dept|departure).*$",
            r"^leav.*(fg|yard).*$"
        ],
        "total_time_raw": [
            r"^total\s*time.*$", r"^total\s*duration.*$", r"^cycle\s*time.*$", r"^turnaround\s*time.*$"
        ],
        "agency": [r"^agency$", r"^contractor$", r"^vendor$", r"^transporter$", r"^carrier$", r"^agency\s*name$"],
        "date": [r"^date$", r"^trip\s*date$", r"^dispatch\s*date$"],
        "remarks": [r"^remark.*$", r"^comment.*$", r"^notes?$", r"^delay\s*reason.*$"],
        "shift": [r"^shift$", r"^work\s*shift$", r"^duty\s*shift$"]
    }

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            self.data_dir = Path(__file__).resolve().parent.parent / "data"
        else:
            self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def scan_files(self) -> List[Path]:
        """
        Scan data directory for Excel workbooks (.xlsx, .xls, .xlsm).
        """
        extensions = ["*.xlsx", "*.xls", "*.xlsm"]
        files = []
        for ext in extensions:
            files.extend(list(self.data_dir.glob(ext)))
        # Filter out temporary Excel lock files (e.g. ~$file.xlsx)
        files = [f for f in files if not f.name.startswith("~$")]
        logger.info(f"Found {len(files)} Excel workbooks in {self.data_dir}")
        return sorted(files)

    def _match_column(self, col_name: str) -> Optional[str]:
        """
        Match a dirty column string against known regex patterns.
        """
        clean_name = str(col_name).strip().lower().replace("_", " ")
        for canonical, patterns in self.COLUMN_MAPPINGS.items():
            for pat in patterns:
                if re.fullmatch(pat, clean_name, re.IGNORECASE) or re.search(pat, clean_name, re.IGNORECASE):
                    return canonical
        return None

    def _find_header_row(self, df_raw: pd.DataFrame, max_search_rows: int = 15) -> Tuple[Optional[int], Dict[int, str]]:
        """
        Detect the true table header row by looking for density of matching canonical columns.
        """
        best_row_idx = None
        best_col_mapping = {}
        max_matches = 0

        search_limit = min(max_search_rows, len(df_raw))
        for r_idx in range(search_limit):
            row_values = df_raw.iloc[r_idx].dropna().tolist()
            if not row_values:
                continue

            current_mapping = {}
            for col_idx, cell_val in enumerate(df_raw.iloc[r_idx]):
                if pd.isna(cell_val):
                    continue
                matched = self._match_column(str(cell_val))
                if matched and matched not in current_mapping.values():
                    current_mapping[col_idx] = matched

            # Header must have at least 3 matching core columns (e.g. trailer, agency, arrival)
            if len(current_mapping) > max_matches and len(current_mapping) >= 3:
                max_matches = len(current_mapping)
                best_row_idx = r_idx
                best_col_mapping = current_mapping

        return best_row_idx, best_col_mapping

    def parse_sheet(self, file_path: Path, sheet_name: str) -> pd.DataFrame:
        """
        Extract and normalize records from a single worksheet.
        """
        try:
            # Read first 1500 rows to find header & data
            df_raw = pd.read_excel(file_path, sheet_name=sheet_name, header=None, engine="openpyxl")
        except Exception as e:
            logger.warning(f"Failed to read sheet '{sheet_name}' from {file_path.name}: {e}")
            return pd.DataFrame()

        if df_raw.empty or len(df_raw) < 2:
            return pd.DataFrame()

        header_row_idx, col_mapping = self._find_header_row(df_raw)
        if header_row_idx is None or not col_mapping:
            logger.debug(f"No valid tabular header detected in sheet '{sheet_name}' of {file_path.name}")
            return pd.DataFrame()

        # Slice data below header
        df_data = df_raw.iloc[header_row_idx + 1:].copy()
        df_data = df_data.rename(columns=col_mapping)
        
        # Keep only recognized mapped columns
        mapped_cols = [c for c in col_mapping.values()]
        df_data = df_data[[c for c in mapped_cols if c in df_data.columns]]

        # Drop rows where critical identifier columns (like trailer_no or agency) are missing
        if "trailer_no" in df_data.columns:
            df_data = df_data[df_data["trailer_no"].notna()]
            df_data = df_data[~df_data["trailer_no"].astype(str).str.contains(r"total|summary|average|remarks|count", case=False, na=False)]
        elif "agency" in df_data.columns:
            df_data = df_data[df_data["agency"].notna()]

        if df_data.empty:
            return pd.DataFrame()

        # Add source lineage metadata
        df_data["source_file"] = file_path.name
        df_data["source_sheet"] = str(sheet_name)

        logger.info(f"Extracted {len(df_data)} valid rows from '{file_path.name}' -> '{sheet_name}'")
        return df_data

    def extract_all(self) -> Tuple[pd.DataFrame, dict]:
        """
        Iterate through all workbooks and sheets, consolidating into a master raw DataFrame.
        """
        files = self.scan_files()
        all_dfs = []
        metadata = {
            "files_processed": 0,
            "sheets_processed": 0,
            "total_rows_extracted": 0,
            "file_list": []
        }

        for f_path in files:
            try:
                wb = openpyxl.load_workbook(f_path, read_only=True, data_only=True)
                sheet_names = wb.sheetnames
                wb.close()
            except Exception as e:
                logger.error(f"Error opening workbook {f_path.name}: {e}")
                continue

            metadata["files_processed"] += 1
            metadata["file_list"].append(f_path.name)

            for s_name in sheet_names:
                metadata["sheets_processed"] += 1
                sheet_df = self.parse_sheet(f_path, s_name)
                if not sheet_df.empty:
                    all_dfs.append(sheet_df)

        if not all_dfs:
            logger.warning("No data extracted from any Excel file.")
            return pd.DataFrame(), metadata

        master_df = pd.concat(all_dfs, ignore_index=True)
        metadata["total_rows_extracted"] = len(master_df)
        logger.info(f"Ingestion complete. Total rows extracted: {len(master_df)}")
        return master_df, metadata

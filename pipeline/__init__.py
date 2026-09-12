"""
ETL Pipeline Package for Ingestion, Transformation, and Relational Loading.
"""
from pipeline.ingestion import ExcelIngestionEngine
from pipeline.transformation import LogisticsDataTransformer
from pipeline.loader import DatabaseLoader

def run_pipeline(data_dir: str = None, db_url: str = None) -> dict:
    """
    End-to-end pipeline execution helper.
    """
    ingestion_engine = ExcelIngestionEngine(data_dir=data_dir)
    transformer = LogisticsDataTransformer()
    loader = DatabaseLoader(db_url=db_url)

    raw_df, ingestion_meta = ingestion_engine.extract_all()
    if raw_df.empty:
        return {
            "status": "empty",
            "message": "No data found or extracted.",
            "ingested_files": ingestion_meta.get("files_processed", 0),
            "records_loaded": 0
        }

    cleaned_df = transformer.transform(raw_df)
    load_result = loader.load(cleaned_df)

    return {
        "status": "success",
        "ingested_files": ingestion_meta.get("files_processed", 0),
        "total_extracted": len(raw_df),
        "records_loaded": load_result.get("inserted", 0),
        "records_updated": load_result.get("updated", 0),
        "records_failed": load_result.get("failed", 0),
        "db_url": loader.db_url
    }

__all__ = ["ExcelIngestionEngine", "LogisticsDataTransformer", "DatabaseLoader", "run_pipeline"]

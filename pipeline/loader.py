"""
Relational Database Loader for SQLite / SQLAlchemy Storage Layer.
"""
import logging
from typing import Dict, List, Optional
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from database.connection import get_engine, get_session, init_db, DB_PATH
from database.models import Trip

logger = logging.getLogger("pipeline.loader")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


class DatabaseLoader:
    """
    Persists cleaned trip records into the SQLite database with transaction safety
    and deduplication handling.
    """

    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url
        self.engine = init_db(db_url)

    def load(self, df: pd.DataFrame, batch_size: int = 500) -> Dict[str, int]:
        """
        Upsert a transformed DataFrame into the `trips` table.
        """
        if df.empty:
            logger.warning("Empty DataFrame passed to DatabaseLoader.load()")
            return {"inserted": 0, "updated": 0, "failed": 0, "total": 0}

        session: Session = get_session(self.db_url)
        stats = {"inserted": 0, "updated": 0, "failed": 0, "total": len(df)}

        try:
            records = df.to_dict(orient="records")
            for i in range(0, len(records), batch_size):
                batch = records[i:i + batch_size]
                batch_uids = [r["trip_uid"] for r in batch if "trip_uid" in r]

                # Find existing trips in this batch
                existing_trips = {
                    t.trip_uid: t for t in session.query(Trip).filter(Trip.trip_uid.in_(batch_uids)).all()
                }

                for raw_row in batch:
                    # Sanitize pandas NaN/NaT into None
                    row = {
                        k: (None if pd.isna(v) else (v.to_pydatetime() if isinstance(v, pd.Timestamp) else v))
                        for k, v in raw_row.items()
                    }
                    uid = row.get("trip_uid")
                    if uid in existing_trips:
                        # Update existing trip
                        trip_obj = existing_trips[uid]
                        for key, val in row.items():
                            if hasattr(trip_obj, key) and key != "id":
                                setattr(trip_obj, key, val)
                        stats["updated"] += 1
                    else:
                        # Insert new trip
                        trip_obj = Trip(**{k: v for k, v in row.items() if hasattr(Trip, k) and k != "id"})
                        session.add(trip_obj)
                        stats["inserted"] += 1

                session.commit()
                logger.info(f"Loaded batch {i // batch_size + 1}: {len(batch)} records processed.")

        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Database error during load: {e}")
            stats["failed"] += len(df) - (stats["inserted"] + stats["updated"])
            raise e
        finally:
            session.close()

        logger.info(
            f"Loading completed. Total: {stats['total']}, Inserted: {stats['inserted']}, "
            f"Updated: {stats['updated']}, Failed: {stats['failed']}"
        )
        return stats

    def fetch_trips_dataframe(self, shift: Optional[str] = None, agency: Optional[str] = None) -> pd.DataFrame:
        """
        Convenience method to query the trips table and return a clean Pandas DataFrame.
        """
        session: Session = get_session(self.db_url)
        try:
            query = session.query(Trip)
            if shift:
                query = query.filter(Trip.shift == shift)
            if agency:
                query = query.filter(Trip.agency == agency)

            trips = query.order_by(Trip.trip_date, Trip.sr_no).all()
            if not trips:
                return pd.DataFrame()

            data = [t.to_dict() for t in trips]
            df = pd.DataFrame(data)
            
            # Cast datetime columns back to datetime
            dt_cols = ["arriving_agency", "leaving_agency", "arriving_waiting", "leaving_waiting", "arriving_fg_yard", "leaving_fg_yard"]
            for col in dt_cols:
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors="coerce")

            return df
        finally:
            session.close()

    def get_trip_count(self) -> int:
        """
        Return the total number of records in the trips table.
        """
        session: Session = get_session(self.db_url)
        try:
            return session.query(Trip).count()
        finally:
            session.close()

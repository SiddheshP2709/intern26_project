"""
Database connection and session management for SQLite / SQLAlchemy.
"""
import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session, declarative_base

# Base directory for database
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "logistics.db"
DEFAULT_DB_URL = f"sqlite:///{DB_PATH}"

_engine = None
_session_factory = None

def get_engine(db_url: str = None):
    """
    Get or create the SQLAlchemy engine.
    """
    global _engine
    if db_url is None:
        db_url = os.getenv("LOGISTICS_DB_URL", DEFAULT_DB_URL)
    
    if _engine is None or str(_engine.url) != db_url:
        # Ensure parent directory exists for SQLite
        if db_url.startswith("sqlite:///"):
            file_path = Path(db_url.replace("sqlite:///", ""))
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
        _engine = create_engine(
            db_url,
            echo=False,
            connect_args={"check_same_thread": False} if "sqlite" in db_url else {}
        )
    return _engine

def get_session(db_url: str = None):
    """
    Provide a scoped session.
    """
    engine = get_engine(db_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return scoped_session(session_factory)()

def init_db(db_url: str = None):
    """
    Initialize database schema (create tables if not existing).
    """
    from database.models import Base
    engine = get_engine(db_url)
    Base.metadata.create_all(bind=engine)
    return engine

"""
SQLAlchemy ORM Data Models for Trip Records and Logistics Operations.
"""
from datetime import datetime, date
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Date,
    Boolean,
    Text,
    Index
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Trip(Base):
    """
    Trip entity representing a single trailer outward cycle from plant agency to FG yard.
    """
    __tablename__ = "trips"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trip_uid = Column(String(120), unique=True, nullable=False, index=True)
    
    # Core transactional identifiers
    sr_no = Column(Integer, nullable=True)
    trailer_no = Column(String(50), nullable=False, index=True)
    trip_date = Column(Date, nullable=False, index=True)
    shift = Column(String(10), nullable=False, index=True)  # 'Shift A', 'Shift B', 'Shift C'
    agency = Column(String(100), nullable=False, index=True)  # 'Guru', 'Tharini', 'Meta', etc.
    remarks = Column(Text, nullable=True)

    # Raw Timestamps
    arriving_agency = Column(DateTime, nullable=True)
    leaving_agency = Column(DateTime, nullable=True)
    arriving_waiting = Column(DateTime, nullable=True)
    leaving_waiting = Column(DateTime, nullable=True)
    arriving_fg_yard = Column(DateTime, nullable=True)
    leaving_fg_yard = Column(DateTime, nullable=True)

    # Process-wise Component Durations (in Minutes)
    loading_time_min = Column(Float, nullable=True)     # (leaving_agency - arriving_agency)
    waiting_time_min = Column(Float, nullable=True)     # (leaving_waiting - arriving_waiting) - NVA
    unloading_time_min = Column(Float, nullable=True)   # (leaving_fg_yard - arriving_fg_yard)
    transit_time_min = Column(Float, nullable=True)     # Inter-station travel duration
    total_cycle_time_min = Column(Float, nullable=True) # Full turnaround duration

    # Operational KPI Flags & Classifications
    is_loading_delayed = Column(Boolean, default=False, index=True)  # True if loading > 45 mins
    is_shift_c_bottleneck = Column(Boolean, default=False, index=True) # True if high NVA in Shift C

    # Audit Metadata
    source_file = Column(String(255), nullable=True)
    source_sheet = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_trip_date_shift", "trip_date", "shift"),
        Index("ix_agency_shift", "agency", "shift"),
    )

    def __repr__(self):
        return (
            f"<Trip(id={self.id}, uid='{self.trip_uid}', trailer='{self.trailer_no}', "
            f"date={self.trip_date}, shift='{self.shift}', agency='{self.agency}', "
            f"cycle_time={self.total_cycle_time_min:.1f}m)>"
        )

    def to_dict(self):
        """Convert model instance to dictionary."""
        return {
            "id": self.id,
            "trip_uid": self.trip_uid,
            "sr_no": self.sr_no,
            "trailer_no": self.trailer_no,
            "trip_date": self.trip_date.isoformat() if isinstance(self.trip_date, (date, datetime)) else self.trip_date,
            "shift": self.shift,
            "agency": self.agency,
            "remarks": self.remarks,
            "arriving_agency": self.arriving_agency.isoformat() if self.arriving_agency else None,
            "leaving_agency": self.leaving_agency.isoformat() if self.leaving_agency else None,
            "arriving_waiting": self.arriving_waiting.isoformat() if self.arriving_waiting else None,
            "leaving_waiting": self.leaving_waiting.isoformat() if self.leaving_waiting else None,
            "arriving_fg_yard": self.arriving_fg_yard.isoformat() if self.arriving_fg_yard else None,
            "leaving_fg_yard": self.leaving_fg_yard.isoformat() if self.leaving_fg_yard else None,
            "loading_time_min": self.loading_time_min,
            "waiting_time_min": self.waiting_time_min,
            "unloading_time_min": self.unloading_time_min,
            "transit_time_min": self.transit_time_min,
            "total_cycle_time_min": self.total_cycle_time_min,
            "is_loading_delayed": self.is_loading_delayed,
            "is_shift_c_bottleneck": self.is_shift_c_bottleneck,
            "source_file": self.source_file,
            "source_sheet": self.source_sheet,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

from sqlalchemy import Column, Integer, String, Enum, DateTime, Float, ForeignKey, Boolean
from sqlalchemy.sql import func
import enum
from .database import Base

class DeviceType(str, enum.Enum):
    WEB = "WEB"
    HARDWARE = "HARDWARE"

class DeviceStatus(str, enum.Enum):
    UP = "UP"
    DOWN = "DOWN"
    WARNING = "WARNING"

class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    device_type = Column(Enum(DeviceType), nullable=False)
    address = Column(String, nullable=False) # URL for WEB, IP for HARDWARE
    status = Column(Enum(DeviceStatus), default=DeviceStatus.UP)
    is_muted = Column(Boolean, default=False)
    failure_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class EventLog(Base):
    __tablename__ = "event_logs"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    old_status = Column(String, nullable=False)
    new_status = Column(String, nullable=False)
    latency = Column(Float, nullable=True) # in milliseconds
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

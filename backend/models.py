from sqlalchemy import Column, Integer, String, Enum, DateTime, Float, ForeignKey, Boolean, TypeDecorator
from sqlalchemy.sql import func
import enum
import zoneinfo
from datetime import datetime, timezone, timedelta
from .database import Base

def get_brasilia_timezone():
    try:
        return zoneinfo.ZoneInfo("America/Sao_Paulo")
    except Exception:
        # Resilient fallback to fixed offset UTC-3 (Brasília timezone)
        return timezone(timedelta(hours=-3), name="America/Sao_Paulo")

def get_brasilia_time() -> datetime:
    return datetime.now(get_brasilia_timezone())

class TZDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=get_brasilia_timezone())
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=get_brasilia_timezone())
        return value

class DeviceType(str, enum.Enum):
    WEB = "WEB"
    HARDWARE = "HARDWARE"

class DeviceStatus(str, enum.Enum):
    UP = "UP"
    DOWN = "DOWN"
    WARNING = "WARNING"
    CRITICAL_OVERLOAD = "CRITICAL_OVERLOAD"

class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    device_type = Column(Enum(DeviceType), nullable=False)
    address = Column(String, nullable=False)  # URL for WEB, IP for HARDWARE
    status = Column(Enum(DeviceStatus), default=DeviceStatus.UP)
    is_muted = Column(Boolean, default=False)
    failure_count = Column(Integer, default=0)
    response_time_ms = Column(Integer, nullable=True, default=None)
    dns_ms = Column(Float, nullable=True, default=None)
    slug_identificador = Column(String(50), nullable=True, index=True, default=None)

    # SNMP fields
    comunidade_snmp = Column(String, nullable=True)
    versao_snmp = Column(String, nullable=True)  # e.g., 'v2c'
    oid_cpu = Column(String, nullable=True)
    ultimo_uso_cpu = Column(Float, nullable=True)
    status_portas = Column(String, nullable=True)  # JSON string

    created_at = Column(TZDateTime(timezone=True), default=get_brasilia_time)
    updated_at = Column(TZDateTime(timezone=True), default=get_brasilia_time, onupdate=get_brasilia_time)


class EventLog(Base):
    __tablename__ = "event_logs"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    old_status = Column(String, nullable=False)
    new_status = Column(String, nullable=False)
    latency = Column(Float, nullable=True)  # in milliseconds
    timestamp = Column(TZDateTime(timezone=True), default=get_brasilia_time)


class PerformanceLog(Base):
    """Stores granular L7 timing breakdown for each WEB device check."""
    __tablename__ = "performance_logs"

    id          = Column(Integer, primary_key=True, index=True)
    device_id   = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)

    # All timings in milliseconds (nullable — segments may be unavailable for plain HTTP)
    dns_ms      = Column(Float, nullable=True)   # DNS resolution
    connect_ms  = Column(Float, nullable=True)   # TCP handshake
    ssl_ms      = Column(Float, nullable=True)   # TLS handshake (0 for plain HTTP)
    ttfb_ms     = Column(Float, nullable=True)   # Time to first byte (server processing)
    download_ms = Column(Float, nullable=True)   # Body download
    total_ms    = Column(Float, nullable=True)   # End-to-end total

    timestamp   = Column(TZDateTime(timezone=True), default=get_brasilia_time, index=True)

from sqlalchemy import Column, Integer, String, Enum, DateTime, Float, ForeignKey, Boolean, TypeDecorator, Index
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
    CRITICAL_LOCK = "CRITICAL_LOCK"

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

    # Keyword Matching (Content Validation)
    validar_texto = Column(Boolean, default=False)
    texto_obrigatorio = Column(String, nullable=True)

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
    # Índice composto para a query de stats por monitor de DB
    # (filtra por db_monitor_id e ordena por timestamp desc).
    __table_args__ = (
        Index("ix_event_logs_db_monitor_timestamp", "db_monitor_id", "timestamp"),
    )

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=True)
    db_monitor_id = Column(Integer, ForeignKey("database_monitors.id", ondelete="CASCADE"), nullable=True)
    old_status = Column(String, nullable=False)
    new_status = Column(String, nullable=False)
    latency = Column(Float, nullable=True)      # latência em ms (eventos de device)
    lock_count = Column(Integer, nullable=True)  # nº de locks (eventos de monitor de DB)
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


class DatabaseMonitor(Base):
    """Monitors a database via the Sentinela API (lock detection)."""
    __tablename__ = "database_monitors"

    id                    = Column(Integer, primary_key=True, index=True)
    nome                  = Column(String, nullable=False)
    endpoint_url          = Column(String, nullable=False)
    status                = Column(String, default="UP")   # UP/DOWN/WARNING/CRITICAL_LOCK
    is_muted              = Column(Boolean, default=False)
    ultimo_total_locks    = Column(Integer, default=0, nullable=True)
    consecutive_lock_count = Column(Integer, default=0)    # rounds with locks > 0 in a row

    created_at  = Column(TZDateTime(timezone=True), default=get_brasilia_time)
    updated_at  = Column(TZDateTime(timezone=True), default=get_brasilia_time, onupdate=get_brasilia_time)


class AppSetting(Base):
    """Configurações globais simples (key/value) compartilhadas por todos os
    clientes — ex.: 'alert_sound_enabled'. create_all cria esta tabela em
    bancos existentes automaticamente (não precisa de migração de coluna)."""
    __tablename__ = "app_settings"

    key   = Column(String, primary_key=True)
    value = Column(String, nullable=True)


class MaintenanceWindow(Base):
    """
    Janela de manutenção programada — durante o intervalo, alertas
    (som/popup/notificação) ficam SUPRIMIDOS, mas o monitoramento continua
    normal e DOWN ainda conta no uptime.

    Tipos:
      - recurrence="none":   janela única; starts_at/ends_at são datetimes absolutos.
      - recurrence="daily":  janela diária; apenas a HORA de starts_at/ends_at é
                             considerada (a parte de data é ignorada). Se a hora
                             final for menor que a inicial, a janela cruza
                             a meia-noite (ex.: 23h → 06h).

    Escopo:
      - device_id=NULL → janela GLOBAL (aplica a todos os devices + monitores).
      - device_id=N    → janela específica para esse Device.
      - db_monitor_id=N→ janela específica para esse DatabaseMonitor.
    """
    __tablename__ = "maintenance_windows"

    id              = Column(Integer, primary_key=True, index=True)
    name            = Column(String, nullable=True)
    device_id       = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=True)
    db_monitor_id   = Column(Integer, ForeignKey("database_monitors.id", ondelete="CASCADE"), nullable=True)
    starts_at       = Column(TZDateTime(timezone=True), nullable=False)
    ends_at         = Column(TZDateTime(timezone=True), nullable=False)
    recurrence      = Column(String, nullable=False, default="none")  # "none" | "daily"
    is_active       = Column(Boolean, default=True)

    created_at      = Column(TZDateTime(timezone=True), default=get_brasilia_time)
    updated_at      = Column(TZDateTime(timezone=True), default=get_brasilia_time, onupdate=get_brasilia_time)

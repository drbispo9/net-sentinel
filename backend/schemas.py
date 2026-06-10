from pydantic import BaseModel, field_validator
from typing import Optional, List
from datetime import datetime
from urllib.parse import urlparse
from .models import DeviceStatus


def _validate_http_url(value: str) -> str:
    """Garante que a string é uma URL http(s) válida. Mantém o tipo str."""
    v = (value or "").strip()
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("endpoint_url deve ser uma URL http(s) válida (ex.: https://host/caminho)")
    return v

class DeviceCreate(BaseModel):
    name: str
    device_type: str
    address: str
    is_muted: Optional[bool] = False
    comunidade_snmp: Optional[str] = None
    versao_snmp: Optional[str] = "v2c"
    oid_cpu: Optional[str] = None
    slug_identificador: Optional[str] = None
    validar_texto: Optional[bool] = False
    texto_obrigatorio: Optional[str] = None

class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    device_type: Optional[str] = None
    address: Optional[str] = None
    is_muted: Optional[bool] = None
    comunidade_snmp: Optional[str] = None
    versao_snmp: Optional[str] = None
    oid_cpu: Optional[str] = None
    slug_identificador: Optional[str] = None
    validar_texto: Optional[bool] = None
    texto_obrigatorio: Optional[str] = None

class DeviceResponse(BaseModel):
    id: int
    name: str
    device_type: str
    address: str
    status: DeviceStatus
    is_muted: bool
    failure_count: int
    response_time_ms: Optional[int] = None
    dns_ms: Optional[float] = None
    comunidade_snmp: Optional[str] = None
    versao_snmp: Optional[str] = None
    oid_cpu: Optional[str] = None
    ultimo_uso_cpu: Optional[float] = None
    status_portas: Optional[str] = None
    slug_identificador: Optional[str] = None
    validar_texto: bool = False
    texto_obrigatorio: Optional[str] = None

    class Config:
        from_attributes = True

class EventLogResponse(BaseModel):
    id: int
    device_id: Optional[int] = None
    db_monitor_id: Optional[int] = None
    device_name: str
    old_status: str
    new_status: str
    latency: Optional[float] = None
    timestamp: datetime

    class Config:
        from_attributes = True

class DeviceStatsResponse(BaseModel):
    uptime_percentage: float
    last_status_change: Optional[datetime] = None
    recent_events: List[EventLogResponse]


class PerformanceLogResponse(BaseModel):
    id: int
    device_id: int
    dns_ms: Optional[float] = None
    connect_ms: Optional[float] = None
    ssl_ms: Optional[float] = None
    ttfb_ms: Optional[float] = None
    download_ms: Optional[float] = None
    total_ms: Optional[float] = None
    timestamp: datetime

    class Config:
        from_attributes = True


# ─── Database Monitor schemas ────────────────────────────────────────────────

class DBMonitorCreate(BaseModel):
    nome: str
    endpoint_url: str
    is_muted: Optional[bool] = False

    @field_validator("endpoint_url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        return _validate_http_url(v)


class DBMonitorUpdate(BaseModel):
    nome: Optional[str] = None
    endpoint_url: Optional[str] = None
    is_muted: Optional[bool] = None

    @field_validator("endpoint_url")
    @classmethod
    def _check_url(cls, v):
        return v if v is None else _validate_http_url(v)


class SettingsResponse(BaseModel):
    alert_sound_enabled: bool


class SettingsUpdate(BaseModel):
    alert_sound_enabled: Optional[bool] = None


class DBMonitorResponse(BaseModel):
    id: int
    nome: str
    endpoint_url: str
    status: str
    is_muted: bool
    ultimo_total_locks: Optional[int] = 0
    consecutive_lock_count: Optional[int] = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ─── Maintenance Windows ─────────────────────────────────────────────────────

class MaintenanceWindowBase(BaseModel):
    name: Optional[str] = None
    device_id: Optional[int] = None        # null = global (não-DB)
    db_monitor_id: Optional[int] = None    # null = sem alvo DB
    starts_at: datetime
    ends_at: datetime
    recurrence: str = "none"               # "none" | "daily"
    is_active: bool = True


class MaintenanceWindowCreate(MaintenanceWindowBase):
    pass


class MaintenanceWindowUpdate(BaseModel):
    name: Optional[str] = None
    device_id: Optional[int] = None
    db_monitor_id: Optional[int] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    recurrence: Optional[str] = None
    is_active: Optional[bool] = None


class MaintenanceWindowResponse(MaintenanceWindowBase):
    id: int
    created_at: datetime
    updated_at: datetime
    is_currently_active: bool = False     # computed: "está vigente agora?"

    class Config:
        from_attributes = True

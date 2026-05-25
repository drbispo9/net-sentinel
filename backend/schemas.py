from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from .models import DeviceStatus

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
    status: str
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


class DBMonitorUpdate(BaseModel):
    nome: Optional[str] = None
    endpoint_url: Optional[str] = None
    is_muted: Optional[bool] = None


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

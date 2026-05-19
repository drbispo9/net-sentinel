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

class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    device_type: Optional[str] = None
    address: Optional[str] = None
    is_muted: Optional[bool] = None
    comunidade_snmp: Optional[str] = None
    versao_snmp: Optional[str] = None
    oid_cpu: Optional[str] = None

class DeviceResponse(BaseModel):
    id: int
    name: str
    device_type: str
    address: str
    status: str
    is_muted: bool
    failure_count: int
    comunidade_snmp: Optional[str] = None
    versao_snmp: Optional[str] = None
    oid_cpu: Optional[str] = None
    ultimo_uso_cpu: Optional[float] = None
    status_portas: Optional[str] = None

    class Config:
        from_attributes = True

class EventLogResponse(BaseModel):
    id: int
    device_id: int
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

import os
import asyncio
from dotenv import load_dotenv

# Load .env variables into the process environment as early as possible,
# before any module reads os.getenv() at import time.
load_dotenv()

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from typing import List
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from .database import engine, Base, get_db
from .models import Device, EventLog, DeviceStatus, DeviceType, PerformanceLog, DatabaseMonitor
from .schemas import (
    DeviceResponse, DeviceCreate, EventLogResponse, DeviceStatsResponse,
    DeviceUpdate, PerformanceLogResponse,
    DBMonitorCreate, DBMonitorUpdate, DBMonitorResponse,
)
from .monitor import MonitorManager
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()
monitor = MonitorManager(websocket_broadcast_callback=manager.broadcast)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup DB
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Resilient column addition for existing sqlite DBs
        try:
            await conn.execute("ALTER TABLE event_logs ADD COLUMN db_monitor_id INTEGER")
        except Exception:
            pass
    
    # Start background monitors
    monitor.running = True
    web_task = asyncio.create_task(monitor.monitor_web_loop())
    hardware_task = asyncio.create_task(monitor.monitor_hardware_loop())
    db_task = asyncio.create_task(monitor.monitor_database_loop())
    
    yield
    
    # Shutdown
    monitor.running = False
    web_task.cancel()
    hardware_task.cancel()
    db_task.cancel()

app = FastAPI(title="NetSentinel API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.get("/api/devices", response_model=List[DeviceResponse])
async def get_devices(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device))
    devices = result.scalars().all()
    return devices

@app.post("/api/devices", response_model=DeviceResponse, status_code=201)
async def create_device(payload: DeviceCreate, db: AsyncSession = Depends(get_db)):
    try:
        device_type = DeviceType(payload.device_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid device_type. Must be WEB or HARDWARE.")

    slug = payload.slug_identificador

    device = Device(
        name=payload.name,
        device_type=device_type,
        address=payload.address,
        status=DeviceStatus.UP,
        is_muted=payload.is_muted or False,
        failure_count=0,
        comunidade_snmp=payload.comunidade_snmp,
        versao_snmp=payload.versao_snmp,
        oid_cpu=payload.oid_cpu,
        slug_identificador=slug,
        validar_texto=payload.validar_texto or False,
        texto_obrigatorio=payload.texto_obrigatorio,
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return device

@app.delete("/api/devices/{device_id}", status_code=204)
async def delete_device(device_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalars().first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    await db.delete(device)
    await db.commit()
    return None

@app.put("/api/devices/{device_id}", response_model=DeviceResponse)
async def update_device(device_id: int, payload: DeviceUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalars().first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
        
    if payload.name is not None:
        device.name = payload.name
    if payload.address is not None:
        device.address = payload.address
    if payload.is_muted is not None:
        device.is_muted = payload.is_muted
    if payload.device_type is not None:
        try:
            device.device_type = DeviceType(payload.device_type)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid device_type")
            
    if payload.comunidade_snmp is not None:
        device.comunidade_snmp = payload.comunidade_snmp
    if payload.versao_snmp is not None:
        device.versao_snmp = payload.versao_snmp
    if payload.oid_cpu is not None:
        device.oid_cpu = payload.oid_cpu
    if payload.slug_identificador is not None:
        device.slug_identificador = payload.slug_identificador
    if payload.validar_texto is not None:
        device.validar_texto = payload.validar_texto
    if payload.texto_obrigatorio is not None:
        device.texto_obrigatorio = payload.texto_obrigatorio
    # Allow clearing the required text when validation is disabled
    if payload.validar_texto is False:
        device.texto_obrigatorio = payload.texto_obrigatorio  # may be None
            
    await db.commit()
    await db.refresh(device)
    return device

@app.get("/api/devices/{device_id}/stats", response_model=DeviceStatsResponse)
async def get_device_stats(device_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalars().first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    stmt = (
        select(EventLog, Device.name.label("device_name"))
        .join(Device, EventLog.device_id == Device.id)
        .where(EventLog.device_id == device_id)
        .order_by(desc(EventLog.timestamp))
        .limit(10)
    )
    events_res = await db.execute(stmt)
    
    events = []
    last_change = None
    
    rows = events_res.all()
    if rows:
        last_change = rows[0][0].timestamp
        for log, d_name in rows:
            events.append(EventLogResponse(
                id=log.id,
                device_id=log.device_id,
                device_name=d_name,
                old_status=log.old_status,
                new_status=log.new_status,
                latency=log.latency,
                timestamp=log.timestamp
            ))
            
    uptime = 100.0
    if device.status == DeviceStatus.DOWN:
        uptime = 98.5
    elif device.status == DeviceStatus.WARNING:
        uptime = 99.5

    return {
        "uptime_percentage": uptime,
        "last_status_change": last_change,
        "recent_events": events
    }

@app.get("/api/devices/{device_id}/performance", response_model=List[PerformanceLogResponse])
async def get_device_performance(device_id: int, limit: int = 20, db: AsyncSession = Depends(get_db)):
    """Return the last `limit` L7 performance records for a WEB device."""
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalars().first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    stmt = (
        select(PerformanceLog)
        .where(PerformanceLog.device_id == device_id)
        .order_by(desc(PerformanceLog.timestamp))
        .limit(max(1, min(limit, 100)))  # clamp between 1 and 100
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()
    return logs


@app.get("/api/devices/{device_id}/report/pdf")
async def get_device_report_pdf(device_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalars().first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    stmt = (
        select(EventLog)
        .where(EventLog.device_id == device_id)
        .order_by(desc(EventLog.timestamp))
        .limit(100)
    )
    events_res = await db.execute(stmt)
    events = events_res.scalars().all()

    from .services.pdf_service import generate_device_pdf
    try:
        pdf_buffer = generate_device_pdf(device, events)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate PDF: {str(e)}")

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=relatorio_{device.name.replace(' ', '_')}.pdf"}
    )


@app.get("/api/events", response_model=List[EventLogResponse])
async def get_events(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(EventLog, Device.name.label("device_name"))
        .join(Device, EventLog.device_id == Device.id)
        .order_by(desc(EventLog.timestamp))
        .limit(50)
    )
    result = await db.execute(stmt)
    
    events = []
    for log, device_name in result.all():
        events.append(EventLogResponse(
            id=log.id,
            device_id=log.device_id,
            device_name=device_name,
            old_status=log.old_status,
            new_status=log.new_status,
            latency=log.latency,
            timestamp=log.timestamp
        ))
    return events

# ── Database Monitor endpoints ────────────────────────────────────────────────

@app.get("/api/db-monitors", response_model=List[DBMonitorResponse])
async def get_db_monitors(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DatabaseMonitor))
    return result.scalars().all()

@app.post("/api/db-monitors", response_model=DBMonitorResponse, status_code=201)
async def create_db_monitor(payload: DBMonitorCreate, db: AsyncSession = Depends(get_db)):
    monitor = DatabaseMonitor(
        nome=payload.nome,
        endpoint_url=payload.endpoint_url,
        is_muted=payload.is_muted or False,
        status="UP",
        ultimo_total_locks=0,
        consecutive_lock_count=0,
    )
    db.add(monitor)
    await db.commit()
    await db.refresh(monitor)
    return monitor

@app.put("/api/db-monitors/{monitor_id}", response_model=DBMonitorResponse)
async def update_db_monitor(monitor_id: int, payload: DBMonitorUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DatabaseMonitor).where(DatabaseMonitor.id == monitor_id))
    monitor = result.scalars().first()
    if not monitor:
        raise HTTPException(status_code=404, detail="DB Monitor not found")
    if payload.nome is not None:
        monitor.nome = payload.nome
    if payload.endpoint_url is not None:
        monitor.endpoint_url = payload.endpoint_url
    if payload.is_muted is not None:
        monitor.is_muted = payload.is_muted
    await db.commit()
    await db.refresh(monitor)
    return monitor

@app.delete("/api/db-monitors/{monitor_id}", status_code=204)
async def delete_db_monitor(monitor_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DatabaseMonitor).where(DatabaseMonitor.id == monitor_id))
    monitor = result.scalars().first()
    if not monitor:
        raise HTTPException(status_code=404, detail="DB Monitor not found")
    await db.delete(monitor)
    await db.commit()


@app.get("/api/db-monitors/{monitor_id}/stats")
async def get_db_monitor_stats(monitor_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DatabaseMonitor).where(DatabaseMonitor.id == monitor_id))
    monitor = result.scalars().first()
    if not monitor:
        raise HTTPException(status_code=404, detail="DB Monitor not found")

    stmt = (
        select(EventLog, DatabaseMonitor.nome.label("device_name"))
        .join(DatabaseMonitor, EventLog.db_monitor_id == DatabaseMonitor.id)
        .where(EventLog.db_monitor_id == monitor_id)
        .order_by(desc(EventLog.timestamp))
        .limit(10)
    )
    events_res = await db.execute(stmt)
    
    events = []
    last_change = None
    
    rows = events_res.all()
    if rows:
        last_change = rows[0][0].timestamp
        for log, m_name in rows:
            events.append({
                "id": log.id,
                "db_monitor_id": log.db_monitor_id,
                "device_name": m_name,
                "old_status": log.old_status,
                "new_status": log.new_status,
                "latency": log.latency,
                "timestamp": log.timestamp
            })
            
    uptime = 100.0
    if monitor.status == "DOWN":
        uptime = 98.5
    elif monitor.status == "WARNING":
        uptime = 99.5
    elif monitor.status == "CRITICAL_LOCK":
        uptime = 95.0

    return {
        "uptime_percentage": uptime,
        "last_status_change": last_change,
        "recent_events": events
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# ── Serve frontend static files (must be last) ──
_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")



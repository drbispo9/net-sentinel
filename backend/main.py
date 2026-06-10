import os
import asyncio
import logging
from datetime import timedelta
from dotenv import load_dotenv

# Load .env variables into the process environment as early as possible,
# before any module reads os.getenv() at import time.
load_dotenv()

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from typing import List, Optional
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from .database import engine, Base, get_db
from .models import (
    Device, EventLog, DeviceStatus, DeviceType, PerformanceLog, DatabaseMonitor,
    MaintenanceWindow, AppSetting, get_brasilia_time,
)
from .schemas import (
    DeviceResponse, DeviceCreate, EventLogResponse, DeviceStatsResponse,
    DeviceUpdate, PerformanceLogResponse,
    DBMonitorCreate, DBMonitorUpdate, DBMonitorResponse,
    MaintenanceWindowCreate, MaintenanceWindowUpdate, MaintenanceWindowResponse,
    SettingsResponse, SettingsUpdate,
)
from .services.maintenance_service import (
    is_window_active,
    load_active_window_candidates,
)
from .monitor import MonitorManager
from .migrations import run_migrations
from .auth import require_api_key, auth_enabled
from .services.network_discovery import (
    scan_network as discover_scan_network,
    get_local_range as discover_get_local_range,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, delete

logger = logging.getLogger(__name__)


# ── Uptime real (calculado a partir do histórico de eventos) ──────────────────

async def _compute_uptime(
    db: AsyncSession,
    *,
    current_status: str,
    device_id: Optional[int] = None,
    db_monitor_id: Optional[int] = None,
    window_hours: int = 168,  # 7 dias
) -> float:
    """
    Calcula a disponibilidade (%) numa janela de tempo a partir das transições
    registradas em EventLog. Conta como INDISPONÍVEL os estados 'DOWN' e
    qualquer 'CRITICAL_*' (CRITICAL_LOCK, CRITICAL_OVERLOAD) — são falhas
    acionáveis. WARNING continua contando como disponível (o alvo respondeu,
    apenas degradado: ex. SSL inválido ou dados obsoletos na origem).
    """
    now = get_brasilia_time()
    window_start = now - timedelta(hours=window_hours)

    stmt = select(EventLog).where(EventLog.timestamp >= window_start)
    if device_id is not None:
        stmt = stmt.where(EventLog.device_id == device_id)
    else:
        stmt = stmt.where(EventLog.db_monitor_id == db_monitor_id)
    stmt = stmt.order_by(EventLog.timestamp.asc())

    events = (await db.execute(stmt)).scalars().all()

    total_seconds = (now - window_start).total_seconds()
    if total_seconds <= 0:
        return 100.0

    # Estado no início da janela: o old_status da primeira transição dentro dela.
    # Sem transições na janela, o estado atual valeu o período todo.
    state = events[0].old_status if events else current_status
    cursor = window_start
    down_seconds = 0.0

    def _is_unavailable(s: str) -> bool:
        # DOWN (inclui "DeviceStatus.DOWN") ou qualquer estado CRITICAL_*.
        s = str(s).upper()
        return s.endswith("DOWN") or "CRITICAL" in s

    for ev in events:
        if _is_unavailable(state):
            down_seconds += (ev.timestamp - cursor).total_seconds()
        cursor = ev.timestamp
        state = ev.new_status

    if _is_unavailable(state):
        down_seconds += (now - cursor).total_seconds()

    uptime = (total_seconds - down_seconds) / total_seconds * 100
    return round(max(0.0, min(100.0, uptime)), 2)

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        dead: List[WebSocket] = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead.append(connection)
        # Remove conexões mortas para a lista não crescer indefinidamente.
        for connection in dead:
            self.disconnect(connection)

manager = ConnectionManager()
monitor = MonitorManager(websocket_broadcast_callback=manager.broadcast)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup DB — cria tabelas novas e sincroniza colunas faltantes (idempotente).
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(run_migrations)

    if not auth_enabled():
        logger.warning(
            "[Auth] API_KEY não definida no ambiente — endpoints de escrita estão "
            "ABERTOS. Defina API_KEY no .env para protegê-los."
        )

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

# CORS — configurável via env CORS_ORIGINS (lista separada por vírgula).
# O frontend autentica por header (X-API-Key), não por cookie, então
# allow_credentials=False — combinação válida com origin "*".
_cors_env = os.getenv("CORS_ORIGINS", "*").strip()
_cors_origins = ["*"] if _cors_env == "*" else [o.strip() for o in _cors_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.get("/api/devices", response_model=List[DeviceResponse])
async def get_devices(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device))
    devices = result.scalars().all()
    return devices

@app.post("/api/devices", response_model=DeviceResponse, status_code=201,
          dependencies=[Depends(require_api_key)])
async def create_device(payload: DeviceCreate, db: AsyncSession = Depends(get_db)):
    try:
        device_type = DeviceType(payload.device_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid device_type. Must be WEB or HARDWARE.")

    slug = payload.slug_identificador

    # Keyword Matching: limpa espaços e normaliza vazio → NULL. Se o flag
    # de validação estiver desligado, o texto também é descartado para
    # manter os dois campos coerentes.
    validar_texto = bool(payload.validar_texto)
    keyword_clean = (payload.texto_obrigatorio or "").strip()
    texto_obrigatorio = keyword_clean if (validar_texto and keyword_clean) else None

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
        validar_texto=validar_texto,
        texto_obrigatorio=texto_obrigatorio,
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return device

@app.delete("/api/devices/{device_id}", status_code=204,
            dependencies=[Depends(require_api_key)])
async def delete_device(device_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalars().first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    # EventLog.device_id NÃO tem ON DELETE CASCADE; com foreign_keys=ON o delete
    # do device falharia por constraint. Removemos os eventos associados antes.
    # (PerformanceLog e MaintenanceWindow já têm cascade, mas o delete explícito
    # é seguro independentemente do estado das FKs.)
    await db.execute(delete(EventLog).where(EventLog.device_id == device_id))
    await db.delete(device)
    await db.commit()
    return None

@app.put("/api/devices/{device_id}", response_model=DeviceResponse,
         dependencies=[Depends(require_api_key)])
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
    # ── Keyword Matching ────────────────────────────────────────────────────
    # Regras:
    #   - Atualiza o flag de validação se o cliente o enviou.
    #   - Atualiza o texto se o cliente o enviou (string vazia ou só espaços
    #     vira NULL, evitando armazenar keyword inválido).
    #   - Se o cliente desativou a validação, o texto é zerado (NULL) — a UI
    #     trata o campo como um par lógico.
    if payload.validar_texto is not None:
        device.validar_texto = payload.validar_texto
    if payload.texto_obrigatorio is not None:
        cleaned = payload.texto_obrigatorio.strip()
        device.texto_obrigatorio = cleaned or None
    if payload.validar_texto is False:
        device.texto_obrigatorio = None
            
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

    uptime = await _compute_uptime(db, current_status=device.status.value, device_id=device_id)

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

@app.post("/api/db-monitors", response_model=DBMonitorResponse, status_code=201,
          dependencies=[Depends(require_api_key)])
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

@app.put("/api/db-monitors/{monitor_id}", response_model=DBMonitorResponse,
         dependencies=[Depends(require_api_key)])
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

@app.delete("/api/db-monitors/{monitor_id}", status_code=204,
            dependencies=[Depends(require_api_key)])
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
            # lock_count é a coluna dedicada; eventos antigos guardavam a
            # contagem no campo 'latency' — fallback para não perder histórico.
            lock_count = log.lock_count
            if lock_count is None and log.latency is not None:
                lock_count = int(log.latency)
            events.append({
                "id": log.id,
                "db_monitor_id": log.db_monitor_id,
                "device_name": m_name,
                "old_status": log.old_status,
                "new_status": log.new_status,
                "lock_count": lock_count,
                "timestamp": log.timestamp
            })
            
    uptime = await _compute_uptime(db, current_status=monitor.status, db_monitor_id=monitor_id)

    return {
        "uptime_percentage": uptime,
        "last_status_change": last_change,
        "recent_events": events
    }


# ── Configurações globais (compartilhadas por todos os clientes) ──────────────

ALERT_SOUND_KEY = "alert_sound_enabled"


async def _get_alert_sound_enabled(db: AsyncSession) -> bool:
    row = await db.get(AppSetting, ALERT_SOUND_KEY)
    if row is None or row.value is None:
        return True  # padrão: som ligado
    return row.value == "1"


@app.get("/api/settings", response_model=SettingsResponse)
async def get_settings(db: AsyncSession = Depends(get_db)):
    return SettingsResponse(alert_sound_enabled=await _get_alert_sound_enabled(db))


@app.put("/api/settings", response_model=SettingsResponse,
         dependencies=[Depends(require_api_key)])
async def update_settings(payload: SettingsUpdate, db: AsyncSession = Depends(get_db)):
    if payload.alert_sound_enabled is not None:
        row = await db.get(AppSetting, ALERT_SOUND_KEY)
        new_value = "1" if payload.alert_sound_enabled else "0"
        if row is None:
            db.add(AppSetting(key=ALERT_SOUND_KEY, value=new_value))
        else:
            row.value = new_value
        await db.commit()
        # Propaga para TODOS os clientes conectados (inclusive a casca Electron).
        await manager.broadcast({
            "type": "settings_update",
            "alert_sound_enabled": payload.alert_sound_enabled,
        })
    return SettingsResponse(alert_sound_enabled=await _get_alert_sound_enabled(db))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ── Maintenance Windows ──────────────────────────────────────────────────────
# Janelas de manutenção programada. Durante o intervalo, alertas (som/popup/
# notificação) ficam suprimidos via flag `muted_by_maintenance` no broadcast.

_VALID_RECURRENCE = {"none", "daily"}


def _validate_window_payload(starts_at, ends_at, recurrence: str) -> None:
    if recurrence not in _VALID_RECURRENCE:
        raise HTTPException(
            status_code=400,
            detail=f"recurrence inválida ({recurrence!r}); use 'none' ou 'daily'",
        )
    # Para "none" o intervalo precisa ser positivo; para "daily" igualdade já é
    # rejeitada no helper. Aqui só barramos o caso óbvio.
    if recurrence == "none" and ends_at <= starts_at:
        raise HTTPException(
            status_code=400,
            detail="ends_at precisa ser maior que starts_at",
        )


def _window_to_response(
    w: MaintenanceWindow, now=None
) -> MaintenanceWindowResponse:
    return MaintenanceWindowResponse(
        id=w.id,
        name=w.name,
        device_id=w.device_id,
        db_monitor_id=w.db_monitor_id,
        starts_at=w.starts_at,
        ends_at=w.ends_at,
        recurrence=w.recurrence,
        is_active=w.is_active,
        created_at=w.created_at,
        updated_at=w.updated_at,
        is_currently_active=is_window_active(w, now),
    )


@app.get("/api/maintenance", response_model=List[MaintenanceWindowResponse])
async def list_maintenance_windows(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MaintenanceWindow).order_by(desc(MaintenanceWindow.created_at))
    )
    now = get_brasilia_time()
    return [_window_to_response(w, now) for w in result.scalars().all()]


@app.post("/api/maintenance", response_model=MaintenanceWindowResponse,
          status_code=201, dependencies=[Depends(require_api_key)])
async def create_maintenance_window(
    payload: MaintenanceWindowCreate, db: AsyncSession = Depends(get_db)
):
    _validate_window_payload(payload.starts_at, payload.ends_at, payload.recurrence)

    # Sanity: não aceitar ambos device_id e db_monitor_id setados (escopo único).
    if payload.device_id is not None and payload.db_monitor_id is not None:
        raise HTTPException(
            status_code=400,
            detail="Informe apenas device_id OU db_monitor_id, não ambos.",
        )

    window = MaintenanceWindow(
        name=(payload.name or "").strip() or None,
        device_id=payload.device_id,
        db_monitor_id=payload.db_monitor_id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        recurrence=payload.recurrence,
        is_active=payload.is_active,
    )
    db.add(window)
    await db.commit()
    await db.refresh(window)
    return _window_to_response(window)


@app.put("/api/maintenance/{window_id}", response_model=MaintenanceWindowResponse,
         dependencies=[Depends(require_api_key)])
async def update_maintenance_window(
    window_id: int,
    payload: MaintenanceWindowUpdate,
    db: AsyncSession = Depends(get_db),
):
    window = (await db.execute(
        select(MaintenanceWindow).where(MaintenanceWindow.id == window_id)
    )).scalars().first()
    if not window:
        raise HTTPException(status_code=404, detail="Janela não encontrada")

    if payload.name is not None:
        window.name = (payload.name or "").strip() or None
    if payload.device_id is not None:
        window.device_id = payload.device_id or None
    if payload.db_monitor_id is not None:
        window.db_monitor_id = payload.db_monitor_id or None
    if payload.starts_at is not None:
        window.starts_at = payload.starts_at
    if payload.ends_at is not None:
        window.ends_at = payload.ends_at
    if payload.recurrence is not None:
        window.recurrence = payload.recurrence
    if payload.is_active is not None:
        window.is_active = payload.is_active

    _validate_window_payload(window.starts_at, window.ends_at, window.recurrence)
    if window.device_id is not None and window.db_monitor_id is not None:
        raise HTTPException(
            status_code=400,
            detail="device_id e db_monitor_id são mutuamente exclusivos.",
        )

    await db.commit()
    await db.refresh(window)
    return _window_to_response(window)


@app.delete("/api/maintenance/{window_id}", status_code=204,
            dependencies=[Depends(require_api_key)])
async def delete_maintenance_window(window_id: int, db: AsyncSession = Depends(get_db)):
    window = (await db.execute(
        select(MaintenanceWindow).where(MaintenanceWindow.id == window_id)
    )).scalars().first()
    if not window:
        raise HTTPException(status_code=404, detail="Janela não encontrada")
    await db.delete(window)
    await db.commit()


@app.get("/api/maintenance/active", response_model=List[MaintenanceWindowResponse])
async def list_active_maintenance_windows(db: AsyncSession = Depends(get_db)):
    """Atalho: só as janelas que estão VIGENTES agora (frontend usa para badges)."""
    windows = await load_active_window_candidates(db)
    now = get_brasilia_time()
    return [_window_to_response(w, now) for w in windows if is_window_active(w, now)]


# ── Infrastructure discovery ─────────────────────────────────────────────────
# Endpoints da aba "Infraestrutura" para varredura ativa de equipamentos de
# rede (switches, roteadores, firewalls, APs) no segmento local. Dispositivos
# descobertos são persistidos como Device(HARDWARE), entrando automaticamente
# no monitor_hardware_loop existente (ping/SNMP).

class InfraDeviceCreate(BaseModel):
    ip: str
    name: Optional[str] = None
    mac: Optional[str] = None
    vendor: Optional[str] = None
    device_type: Optional[str] = None      # switch/router/firewall/access_point/unknown
    manageable_via: Optional[List[str]] = None
    open_ports: Optional[List[dict]] = None


@app.get("/api/infrastructure/network/local-range")
async def infrastructure_local_range():
    """Devolve o IP local e um /24 sugerido para a varredura."""
    return await discover_get_local_range()


@app.post("/api/infrastructure/devices", response_model=DeviceResponse, status_code=201,
          dependencies=[Depends(require_api_key)])
async def add_infrastructure_device(payload: InfraDeviceCreate, db: AsyncSession = Depends(get_db)):
    """
    Persiste um dispositivo descoberto como Device(HARDWARE). Rejeita IPs
    já cadastrados para evitar duplicação no monitor.
    """
    ip = (payload.ip or "").strip()
    if not ip:
        raise HTTPException(status_code=400, detail="Campo 'ip' é obrigatório")

    existing = (await db.execute(select(Device).where(Device.address == ip))).scalars().first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Já existe um dispositivo cadastrado com o IP {ip}",
        )

    # Nome amigável: prefere o que o usuário enviou; senão "Vendor Type (IP)".
    type_labels = {
        "switch":       "Switch",
        "router":       "Roteador",
        "firewall":     "Firewall",
        "access_point": "Access Point",
        "unknown":      "Equipamento",
    }
    inferred = type_labels.get((payload.device_type or "unknown").lower(), "Equipamento")
    default_name = f"{payload.vendor or 'Rede'} {inferred} ({ip})"
    name = (payload.name or "").strip() or default_name

    device = Device(
        name=name,
        device_type=DeviceType.HARDWARE,
        address=ip,
        status=DeviceStatus.UP,
        is_muted=False,
        failure_count=0,
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return device


@app.websocket("/ws/network-scan")
async def ws_network_scan(websocket: WebSocket):
    """
    Recebe {"network": "x.x.x.x/24"} e emite eventos em tempo real:
      scan_started, host_scanned, host_found, scan_complete, error.
    Fechar a conexão cancela a varredura em curso.
    """
    await websocket.accept()
    scan_task: Optional[asyncio.Task] = None
    watch_task: Optional[asyncio.Task] = None

    try:
        init = await websocket.receive_json()
        network = (init.get("network") or "").strip() if isinstance(init, dict) else ""
        if not network:
            await websocket.send_json({"type": "error", "message": "Campo 'network' é obrigatório"})
            return

        scan_task = asyncio.create_task(discover_scan_network(network, websocket))

        # Backup-watch: se o cliente mandar QUALQUER coisa (incluindo close
        # frame), saímos. As próprias chamadas send_json dentro do scan já
        # falham silenciosamente em desconexão; este loop garante o
        # cancelamento da task em paralelo.
        async def watch_close():
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            except Exception:
                pass

        watch_task = asyncio.create_task(watch_close())

        done, pending = await asyncio.wait(
            {scan_task, watch_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        # Propaga exceção da scan_task se houver
        for t in done:
            if t is scan_task:
                exc = t.exception()
                if exc and not isinstance(exc, asyncio.CancelledError):
                    logger.exception("[NetScan] scan task failed: %s", exc)

    except WebSocketDisconnect:
        logger.debug("[NetScan] cliente desconectou durante init")
    except Exception as e:
        logger.exception("[NetScan] erro inesperado: %s", e)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        if scan_task and not scan_task.done():
            scan_task.cancel()
        if watch_task and not watch_task.done():
            watch_task.cancel()
        try:
            await websocket.close()
        except Exception:
            pass


# ── Serve frontend static files (must be last) ──
class NoCacheStaticFiles(StaticFiles):
    """StaticFiles que envia 'Cache-Control: no-cache'.

    Sem isso, o StaticFiles não manda Cache-Control e o navegador aplica cache
    heurístico: ao recarregar, o index.html é revalidado mas o app.js/styles.css
    vêm do cache local SEM revalidar — então edições do frontend não apareciam
    no reload normal (só com Ctrl+Shift+R). 'no-cache' força revalidação via
    ETag a cada carga (retorna 304 barato quando nada mudou).
    """
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount("/", NoCacheStaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")



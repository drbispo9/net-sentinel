"""Testes da API e das regras de negócio corrigidas."""

import ssl
from datetime import timedelta

import pytest

from backend.models import (
    Device, EventLog, DeviceType, DeviceStatus, DatabaseMonitor, get_brasilia_time,
)
from backend.main import _compute_uptime
from backend.monitor import _is_ssl_error


# ── CRUD básico ───────────────────────────────────────────────────────────────

async def test_list_devices_empty(client):
    r = await client.get("/api/devices")
    assert r.status_code == 200
    assert r.json() == []


async def test_create_and_list_device(client):
    payload = {"name": "Google", "device_type": "WEB", "address": "https://google.com"}
    r = await client.post("/api/devices", json=payload)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Google"
    assert body["status"] == "UP"

    r2 = await client.get("/api/devices")
    assert len(r2.json()) == 1


async def test_delete_device_with_event_logs(client, session_factory, monkeypatch):
    """Regressão: deletar device COM histórico de eventos não pode dar 500
    (EventLog.device_id sem cascade + foreign_keys=ON)."""
    monkeypatch.delenv("API_KEY", raising=False)
    created = await client.post(
        "/api/devices", json={"name": "sw", "device_type": "HARDWARE", "address": "10.0.0.1"}
    )
    did = created.json()["id"]
    async with session_factory() as s:
        s.add(EventLog(device_id=did, old_status="UP", new_status="DOWN", latency=12.0))
        await s.commit()

    r = await client.delete(f"/api/devices/{did}")
    assert r.status_code == 204
    assert (await client.get("/api/devices")).json() == []


async def test_invalid_device_type(client):
    r = await client.post(
        "/api/devices",
        json={"name": "x", "device_type": "FOO", "address": "y"},
    )
    assert r.status_code == 400


# ── Autenticação por API_KEY ───────────────────────────────────────────────────

async def test_write_open_when_no_api_key(client, monkeypatch):
    """Sem API_KEY no ambiente, a escrita fica aberta."""
    monkeypatch.delenv("API_KEY", raising=False)
    r = await client.post(
        "/api/devices",
        json={"name": "x", "device_type": "WEB", "address": "y"},
    )
    assert r.status_code == 201


async def test_write_requires_key_when_configured(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret123")
    body = {"name": "x", "device_type": "WEB", "address": "y"}

    # Sem header → 401
    assert (await client.post("/api/devices", json=body)).status_code == 401

    # Header errado → 401
    r = await client.post("/api/devices", json=body, headers={"X-API-Key": "wrong"})
    assert r.status_code == 401

    # Header correto → 201
    r = await client.post("/api/devices", json=body, headers={"X-API-Key": "secret123"})
    assert r.status_code == 201


async def test_reads_open_even_with_api_key(client, monkeypatch):
    """GET continua aberto mesmo com API_KEY configurada."""
    monkeypatch.setenv("API_KEY", "secret123")
    assert (await client.get("/api/devices")).status_code == 200


# ── Uptime real ────────────────────────────────────────────────────────────────

async def test_compute_uptime_counts_only_down(session_factory):
    now = get_brasilia_time()
    async with session_factory() as s:
        dev = Device(name="d", device_type=DeviceType.WEB, address="x", status=DeviceStatus.UP)
        s.add(dev)
        await s.commit()
        await s.refresh(dev)

        # 1h de DOWN dentro de uma janela de 168h (7 dias).
        s.add(EventLog(device_id=dev.id, old_status="UP", new_status="DOWN",
                       timestamp=now - timedelta(hours=48)))
        s.add(EventLog(device_id=dev.id, old_status="DOWN", new_status="UP",
                       timestamp=now - timedelta(hours=47)))
        await s.commit()

        uptime = await _compute_uptime(s, current_status="UP", device_id=dev.id, window_hours=168)

    # (168 - 1) / 168 * 100 ≈ 99.40
    assert 99.3 < uptime < 99.5


async def test_compute_uptime_full_when_no_events(session_factory):
    async with session_factory() as s:
        dev = Device(name="d2", device_type=DeviceType.WEB, address="x", status=DeviceStatus.UP)
        s.add(dev)
        await s.commit()
        await s.refresh(dev)
        uptime = await _compute_uptime(s, current_status="UP", device_id=dev.id)
    assert uptime == 100.0


async def test_warning_status_counts_as_available(session_factory):
    """WARNING (ex.: SSL inválido) NÃO conta como indisponibilidade."""
    now = get_brasilia_time()
    async with session_factory() as s:
        dev = Device(name="d3", device_type=DeviceType.WEB, address="x", status=DeviceStatus.WARNING)
        s.add(dev)
        await s.commit()
        await s.refresh(dev)
        s.add(EventLog(device_id=dev.id, old_status="UP", new_status="WARNING",
                       timestamp=now - timedelta(hours=10)))
        await s.commit()
        uptime = await _compute_uptime(s, current_status="WARNING", device_id=dev.id)
    assert uptime == 100.0


async def test_critical_lock_counts_as_down(session_factory):
    """CRITICAL_LOCK (lock sustentado) conta como INDISPONÍVEL no uptime."""
    now = get_brasilia_time()
    async with session_factory() as s:
        mon = DatabaseMonitor(nome="db", endpoint_url="http://x", status="UP")
        s.add(mon)
        await s.commit()
        await s.refresh(mon)

        # 1h em CRITICAL_LOCK dentro de uma janela de 168h.
        s.add(EventLog(db_monitor_id=mon.id, old_status="UP", new_status="CRITICAL_LOCK",
                       timestamp=now - timedelta(hours=48)))
        s.add(EventLog(db_monitor_id=mon.id, old_status="CRITICAL_LOCK", new_status="UP",
                       timestamp=now - timedelta(hours=47)))
        await s.commit()

        uptime = await _compute_uptime(s, current_status="UP", db_monitor_id=mon.id, window_hours=168)

    # (168 - 1) / 168 * 100 ≈ 99.40 — a hora em CRITICAL_LOCK pesou como down.
    assert 99.3 < uptime < 99.5


async def test_currently_critical_lock_counts_as_down(session_factory):
    """Sem transições na janela, o estado atual CRITICAL_LOCK vale o período todo → 0%."""
    async with session_factory() as s:
        mon = DatabaseMonitor(nome="db2", endpoint_url="http://x", status="CRITICAL_LOCK")
        s.add(mon)
        await s.commit()
        await s.refresh(mon)
        uptime = await _compute_uptime(s, current_status="CRITICAL_LOCK", db_monitor_id=mon.id)
    assert uptime == 0.0


# ── Configurações globais (som de alerta) ─────────────────────────────────────

async def test_settings_default_sound_on(client):
    r = await client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["alert_sound_enabled"] is True


async def test_settings_toggle_persists(client, monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    r = await client.put("/api/settings", json={"alert_sound_enabled": False})
    assert r.status_code == 200
    assert r.json()["alert_sound_enabled"] is False
    # Persistiu para os próximos GETs (e, em produção, para novos clientes).
    r2 = await client.get("/api/settings")
    assert r2.json()["alert_sound_enabled"] is False


async def test_settings_update_requires_key_when_configured(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret123")
    assert (await client.put("/api/settings", json={"alert_sound_enabled": False})).status_code == 401


# ── Detecção de SSL ──────────────────────────────────────────────────────────────

def test_is_ssl_error_detects_certificate_problems():
    assert _is_ssl_error(ssl.SSLCertVerificationError("certificate verify failed"))
    assert _is_ssl_error(ssl.SSLError("ssl handshake failed"))


def test_is_ssl_error_ignores_non_ssl():
    assert not _is_ssl_error(ValueError("timeout"))
    assert not _is_ssl_error(ConnectionRefusedError("refused"))


# ── DB Monitor: validação de endpoint_url ─────────────────────────────────────

async def test_create_db_monitor_rejects_invalid_url(client, monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    r = await client.post("/api/db-monitors", json={"nome": "x", "endpoint_url": "nao-eh-url"})
    assert r.status_code == 422


async def test_create_db_monitor_accepts_valid_url(client, monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    r = await client.post(
        "/api/db-monitors", json={"nome": "x", "endpoint_url": "https://host/api/locks"}
    )
    assert r.status_code == 201
    assert r.json()["endpoint_url"] == "https://host/api/locks"


async def test_update_db_monitor_rejects_invalid_url(client, monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    created = await client.post(
        "/api/db-monitors", json={"nome": "x", "endpoint_url": "http://host/locks"}
    )
    mid = created.json()["id"]
    r = await client.put(f"/api/db-monitors/{mid}", json={"endpoint_url": "ftp://host"})
    assert r.status_code == 422


# ── DB Monitor: lock_count nos stats ──────────────────────────────────────────

async def test_db_stats_returns_lock_count(client, session_factory, monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    created = await client.post(
        "/api/db-monitors", json={"nome": "db", "endpoint_url": "http://host:8000/locks"}
    )
    mid = created.json()["id"]
    async with session_factory() as s:
        s.add(EventLog(db_monitor_id=mid, old_status="UP",
                       new_status="CRITICAL_LOCK", lock_count=3))
        await s.commit()
    stats = (await client.get(f"/api/db-monitors/{mid}/stats")).json()
    assert stats["recent_events"][0]["lock_count"] == 3


async def test_db_stats_lock_count_fallback_from_latency(client, session_factory, monkeypatch):
    """Eventos históricos guardavam a contagem em 'latency' → fallback."""
    monkeypatch.delenv("API_KEY", raising=False)
    created = await client.post(
        "/api/db-monitors", json={"nome": "db2", "endpoint_url": "http://host/locks"}
    )
    mid = created.json()["id"]
    async with session_factory() as s:
        s.add(EventLog(db_monitor_id=mid, old_status="UP",
                       new_status="CRITICAL_LOCK", latency=5.0))  # lock_count fica NULL
        await s.commit()
    stats = (await client.get(f"/api/db-monitors/{mid}/stats")).json()
    assert stats["recent_events"][0]["lock_count"] == 5

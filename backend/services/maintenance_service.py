"""
backend/services/maintenance_service.py
───────────────────────────────────────
Avaliação de janelas de manutenção programada.

Durante uma janela ativa, alertas (som, popup, notificação OS) ficam
suprimidos via flag `muted_by_maintenance` no broadcast WebSocket.
O monitoramento continua normal — status é gravado, eventos são logados,
e o uptime ainda considera DOWN como indisponibilidade (decisão explícita
do produto: relatório SLA reflete realidade).

Regras das janelas:
  - recurrence="none": starts_at ≤ now ≤ ends_at (datetimes absolutos).
  - recurrence="daily": apenas a HORA-do-dia de starts_at/ends_at importa;
    se end_time < start_time, a janela cruza meia-noite (ex.: 23h → 06h).
  - device_id=NULL e db_monitor_id=NULL → janela GLOBAL.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import MaintenanceWindow, get_brasilia_time

logger = logging.getLogger(__name__)


def _within_daily(now: datetime, starts_at: datetime, ends_at: datetime) -> bool:
    """
    Janela DIÁRIA: compara só hora-do-dia. Lida com wrap-around (23h→06h).
    """
    now_t   = now.time()
    start_t = starts_at.time()
    end_t   = ends_at.time()

    if start_t == end_t:
        return False  # janela degenerada; ignorar

    if end_t > start_t:
        # Mesmo dia: 09:00 → 17:00
        return start_t <= now_t <= end_t
    else:
        # Cruza meia-noite: 23:00 → 06:00
        return now_t >= start_t or now_t <= end_t


def is_window_active(window: MaintenanceWindow, now: datetime | None = None) -> bool:
    """Booleano puro: a janela está vigente AGORA?"""
    if not window.is_active:
        return False

    now = now or get_brasilia_time()

    # Garante timezone-awareness antes de comparar (SQLite às vezes devolve
    # datetimes naive mesmo com TZDateTime — defesa em profundidade).
    if now.tzinfo is None:
        now = now.replace(tzinfo=get_brasilia_time().tzinfo)

    if window.recurrence == "none":
        s = window.starts_at
        e = window.ends_at
        if s.tzinfo is None:
            s = s.replace(tzinfo=now.tzinfo)
        if e.tzinfo is None:
            e = e.replace(tzinfo=now.tzinfo)
        return s <= now <= e

    if window.recurrence == "daily":
        return _within_daily(now, window.starts_at, window.ends_at)

    return False


def _window_targets_device(window: MaintenanceWindow, *, device_id: int | None,
                           db_monitor_id: int | None) -> bool:
    """
    A janela cobre o alvo informado?

    Regras:
      - Janela com device_id/db_monitor_id NULL ambos → GLOBAL: cobre tudo.
      - Janela com device_id setado → cobre só esse device.
      - Janela com db_monitor_id setado → cobre só esse monitor de DB.
    """
    is_global = window.device_id is None and window.db_monitor_id is None
    if is_global:
        return True
    if device_id is not None and window.device_id == device_id:
        return True
    if db_monitor_id is not None and window.db_monitor_id == db_monitor_id:
        return True
    return False


def is_target_under_maintenance(
    windows: Iterable[MaintenanceWindow],
    *,
    device_id: int | None = None,
    db_monitor_id: int | None = None,
    now: datetime | None = None,
) -> bool:
    """
    Há ALGUMA janela ativa cobrindo o alvo agora?
    Recebe lista já carregada para evitar query no hot-path do monitor.
    """
    now = now or get_brasilia_time()
    for w in windows:
        if not _window_targets_device(w, device_id=device_id, db_monitor_id=db_monitor_id):
            continue
        if is_window_active(w, now):
            return True
    return False


async def load_active_window_candidates(session: AsyncSession) -> list[MaintenanceWindow]:
    """
    Carrega todas as janelas com is_active=True. O filtro de "está vigente AGORA"
    é feito em Python (a comparação de hora-do-dia para janelas diárias é
    complicada para fazer em SQL portável).
    """
    result = await session.execute(
        select(MaintenanceWindow).where(MaintenanceWindow.is_active.is_(True))
    )
    return list(result.scalars().all())


async def is_device_under_maintenance(
    session: AsyncSession,
    *,
    device_id: int | None = None,
    db_monitor_id: int | None = None,
) -> bool:
    """
    Conveniência: carrega janelas ativas e avalia o alvo. Usar quando o
    chamador não tem a lista em mãos; no hot-path do monitor, preferir
    `is_target_under_maintenance` com a lista já carregada.
    """
    windows = await load_active_window_candidates(session)
    return is_target_under_maintenance(
        windows, device_id=device_id, db_monitor_id=db_monitor_id
    )

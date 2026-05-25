"""
services/database_service.py
─────────────────────────────
Async lock monitoring service that consumes the Sentinela API.

Expected JSON format from the Sentinela endpoint:
{
  "updatedAt": "2026-05-25T12:34:56Z",
  "rows": [
    {
      "sessionId": 53,
      "blockingSessionId": 51,
      "status": "suspended",
      "command": "SELECT",
      "waitType": "LCK_M_S",
      "waitTime": 12458,
      "databaseName": "BRConselhos_OABGO"
    }
  ],
  "error": null   ← or "Login failed for user '...'" when Sentinela can't connect
}

Status escalation logic:
  HTTP failure / non-200  → DOWN
  error != null           → DOWN  (Sentinela can't reach the database)
  len(rows) > 0 (1st round) → WARNING + save lock count
  len(rows) > 0 (2nd+ round) → CRITICAL_LOCK
  rows == [] AND error is null → UP
"""

import logging
import httpx
from sqlalchemy import select

from ..database import AsyncSessionLocal
from ..models import DatabaseMonitor

logger = logging.getLogger(__name__)


async def check_database_lock_monitor(monitor_id: int, broadcast_callback) -> None:
    """Check a single DatabaseMonitor and broadcast the result via WebSocket."""

    async with AsyncSessionLocal() as session:
        monitor = await session.get(DatabaseMonitor, monitor_id)
        if not monitor:
            return

        old_status = monitor.status
        new_status = "UP"

        try:
            async with httpx.AsyncClient(
                verify=False,
                timeout=httpx.Timeout(15.0),
                follow_redirects=True,
            ) as client:
                resp = await client.get(monitor.endpoint_url)

            # ── Step 1: HTTP-level check ────────────────────────────────────
            if resp.status_code != 200:
                new_status = "DOWN"
                monitor.consecutive_lock_count = 0
                monitor.ultimo_total_locks = 0
                logger.warning(
                    f"[DB Monitor] {monitor.nome}: HTTP {resp.status_code} "
                    f"from {monitor.endpoint_url}"
                )
            else:
                data = resp.json()
                error = data.get("error")          # null → None in Python
                rows  = data.get("rows", []) or []

                # ── Step 2: Sentinela connection error ──────────────────────
                if error:
                    new_status = "DOWN"
                    monitor.consecutive_lock_count = 0
                    monitor.ultimo_total_locks = 0
                    logger.warning(
                        f"[DB Monitor] {monitor.nome}: Sentinela reportou erro: {error}"
                    )

                # ── Step 3: Locks detected ──────────────────────────────────
                elif len(rows) > 0:
                    lock_count = len(rows)
                    monitor.ultimo_total_locks = lock_count
                    monitor.consecutive_lock_count = (monitor.consecutive_lock_count or 0) + 1

                    if monitor.consecutive_lock_count >= 2:
                        new_status = "CRITICAL_LOCK"
                    else:
                        new_status = "WARNING"   # 1st round — alert, not critical yet

                    logger.warning(
                        f"[DB Monitor] {monitor.nome}: {lock_count} lock(s) detectado(s) "
                        f"(rodada {monitor.consecutive_lock_count}) → {new_status}"
                    )

                # ── Step 4: Clean — no locks, no error ─────────────────────
                else:
                    new_status = "UP"
                    monitor.ultimo_total_locks = 0
                    monitor.consecutive_lock_count = 0
                    logger.debug(f"[DB Monitor] {monitor.nome}: OK — sem locks")

        except Exception as exc:
            new_status = "DOWN"
            monitor.consecutive_lock_count = 0
            monitor.ultimo_total_locks = 0
            logger.error(f"[DB Monitor] {monitor.nome}: Erro na checagem: {exc}")

        # ── Persist ─────────────────────────────────────────────────────────
        status_changed = old_status != new_status
        if status_changed:
            from ..models import EventLog
            event_log = EventLog(
                db_monitor_id=monitor.id,
                old_status=old_status,
                new_status=new_status,
                latency=float(monitor.ultimo_total_locks or 0),
            )
            session.add(event_log)

        monitor.status = new_status
        await session.commit()

        # ── WebSocket broadcast ──────────────────────────────────────────────

        if new_status == "CRITICAL_LOCK":
            priority = "critical"
        elif new_status in ("DOWN", "WARNING"):
            priority = "high"
        else:
            priority = "info"

        await broadcast_callback({
            "type": "db_status_change" if status_changed else "db_status_update",
            "priority": priority,
            "monitor_id": monitor.id,
            "monitor_name": monitor.nome,
            "status": new_status,
            "is_muted": monitor.is_muted,
            "ultimo_total_locks": monitor.ultimo_total_locks or 0,
        })

        if status_changed:
            logger.info(
                f"[DB Monitor] {monitor.nome}: {old_status} → {new_status}"
            )

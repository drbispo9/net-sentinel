"""
services/database_service.py
─────────────────────────────
Serviço assíncrono de monitoramento de *bloqueios* (locks) que consome a API
Sentinela.

⚠️ Nota de terminologia: estes endpoints retornam as **sessões bloqueadas /
em espera** no instante da consulta (bloqueio sustentado), NÃO "deadlocks" no
sentido técnico — um deadlock real é resolvido pelo próprio SGBD em segundos e
não apareceria como linha persistente aqui. O que detectamos é contenção/
travamento sustentado, que é o problema acionável.

Formato JSON esperado do endpoint Sentinela:
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
  "error": null   ← ou "Login failed for user '...'" quando a Sentinela não conecta
}

Escalada de status:
  Falha de rede / não-200 / JSON inesponível  → DOWN  (após N tentativas)
  error != null                               → DOWN  (após N tentativas)
  campo 'rows' ausente/inválido               → DOWN  (fail-closed: não confia)
  rows > 0                                      → CRITICAL_LOCK (imediato)
  rows == [] mas 'updatedAt' obsoleto          → WARNING (dados travados na origem)
  rows == [] e error null                      → UP

Sobre o CRITICAL_LOCK imediato: a API Sentinela já testa o lock 6 vezes
internamente e só devolve linhas em 'rows' quando o bloqueio persiste nas 6
checagens. Portanto, quando 'rows' vem preenchido, o lock JÁ é sustentado/
confirmado — não faz sentido esperar uma 2ª rodada nossa de polling (atrasaria
o alerta crítico ~1 intervalo à toa). Ainda assim contamos consecutive_lock_count
como informação (há quantas rodadas nossas o lock persiste).

Variáveis de ambiente (todas opcionais):
  DB_LOCK_MIN_WAIT_MS     Ignora linhas com waitTime abaixo desse valor (ms).
                          0 (padrão) = conta todos os bloqueios.
  DB_DATA_MAX_AGE_SECONDS Se > 0, marca WARNING quando 'updatedAt' estiver mais
                          velho que isso (dados possivelmente travados na origem).
                          0 (padrão) = checagem desabilitada.
"""

import os
import asyncio
import logging
from datetime import datetime, timezone

import httpx

from ..database import AsyncSessionLocal
from ..models import DatabaseMonitor

logger = logging.getLogger(__name__)

# Retry dentro de uma rodada — absorve blips momentâneos de rede antes de
# declarar DOWN (mesma filosofia do monitor Web).
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2
REQUEST_TIMEOUT_SECONDS = 10.0


# ─────────────────────────────────────────────────────────────────────────────
# Helpers puros (testáveis sem rede / banco)
# ─────────────────────────────────────────────────────────────────────────────

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _classify_response(status_code: int, payload):
    """
    Classifica uma resposta da Sentinela.
    Retorna (ok: bool, info) — info é o dict de dados quando ok, ou uma string
    com o motivo da falha caso contrário.

    Fail-closed: se o corpo não tiver o formato esperado (campo 'rows' como
    lista), NÃO assumimos "sem locks" — tratamos como falha, para o monitor não
    ficar cego silenciosamente caso o contrato da Sentinela mude.
    """
    if status_code != 200:
        return False, f"HTTP {status_code}"
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        return False, "formato inesperado (campo 'rows' ausente ou inválido)"
    if payload.get("error"):
        return False, f"Sentinela reportou erro: {payload.get('error')}"
    return True, payload


def _row_wait_ms(row) -> float:
    """Extrai waitTime (ms) de uma linha de forma resiliente."""
    try:
        return float(row.get("waitTime") or 0)
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _count_locks(rows, min_wait_ms: int) -> int:
    """Conta bloqueios, ignorando os abaixo do limiar de waitTime (se configurado)."""
    if min_wait_ms > 0:
        return sum(1 for r in rows if _row_wait_ms(r) >= min_wait_ms)
    return len(rows)


def _is_stale(updated_at, max_age_seconds: int) -> bool:
    """
    True se 'updatedAt' for mais velho que max_age_seconds. Desabilitado quando
    max_age_seconds <= 0. Parsing defensivo: qualquer problema → não-stale
    (nunca derruba o fluxo principal por causa de um timestamp estranho).
    """
    if max_age_seconds <= 0 or not updated_at:
        return False
    try:
        ts = str(updated_at).replace("Z", "+00:00")
        # Python não aceita frações com mais de 6 dígitos (nanossegundos) —
        # trunca para microssegundos preservando o offset de fuso.
        if "." in ts:
            head, _, tail = ts.partition(".")
            frac, offset = "", ""
            for i, ch in enumerate(tail):
                if ch.isdigit():
                    frac += ch
                else:
                    offset = tail[i:]
                    break
            ts = f"{head}.{frac[:6]}{offset}"
        parsed = datetime.fromisoformat(ts)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - parsed).total_seconds()
        return age > max_age_seconds
    except Exception:
        return False


def _evaluate_locks(monitor: DatabaseMonitor, data: dict) -> str:
    """
    Lê os dados (já validados) e atualiza os contadores do monitor, devolvendo
    o novo status (UP / WARNING / CRITICAL_LOCK).
    """
    rows = data.get("rows") or []
    lock_count = _count_locks(rows, _env_int("DB_LOCK_MIN_WAIT_MS", 0))

    if lock_count > 0:
        monitor.ultimo_total_locks = lock_count
        # consecutive_lock_count é mantido apenas como informação (há quantas
        # rodadas nossas o lock persiste). O status NÃO depende mais dele: a
        # Sentinela já confirmou o lock em 6 checagens internas, então rows>0
        # é um lock sustentado e vai direto para CRITICAL_LOCK.
        monitor.consecutive_lock_count = (monitor.consecutive_lock_count or 0) + 1
        logger.warning(
            f"[DB Monitor] {monitor.nome}: {lock_count} bloqueio(s) confirmado(s) "
            f"pela Sentinela (rodada {monitor.consecutive_lock_count}) → CRITICAL_LOCK"
        )
        return "CRITICAL_LOCK"

    # Sem bloqueios.
    monitor.ultimo_total_locks = 0
    monitor.consecutive_lock_count = 0

    if _is_stale(data.get("updatedAt"), _env_int("DB_DATA_MAX_AGE_SECONDS", 0)):
        logger.warning(
            f"[DB Monitor] {monitor.nome}: sem bloqueios, mas 'updatedAt' está "
            f"desatualizado — dados possivelmente travados na origem."
        )
        return "WARNING"

    logger.debug(f"[DB Monitor] {monitor.nome}: OK — sem bloqueios")
    return "UP"


# ─────────────────────────────────────────────────────────────────────────────
# Checagem principal
# ─────────────────────────────────────────────────────────────────────────────

async def check_database_lock_monitor(monitor_id: int, broadcast_callback) -> None:
    """Checa um DatabaseMonitor (com retry) e transmite o resultado via WebSocket."""

    async with AsyncSessionLocal() as session:
        monitor = await session.get(DatabaseMonitor, monitor_id)
        if not monitor:
            return

        old_status = monitor.status
        timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)

        result = None             # dict de dados numa leitura limpa
        failure_reason = None     # motivo da falha (para o log de DOWN)

        # ── Retry: tenta obter uma leitura limpa antes de declarar DOWN ──────
        # Um único cliente para todas as tentativas (reaproveita conexão/config).
        async with httpx.AsyncClient(
            verify=False,
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    resp = await client.get(monitor.endpoint_url)

                    payload = None
                    if resp.status_code == 200:
                        try:
                            payload = resp.json()
                        except Exception:
                            payload = None  # corpo não-JSON → cai no fail-closed

                    ok, info = _classify_response(resp.status_code, payload)
                    if ok:
                        result = info
                        failure_reason = None
                        break
                    failure_reason = info

                except Exception as exc:
                    failure_reason = f"falha de conexão: {exc}"

                if attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS)

        # ── Determina o novo status ──────────────────────────────────────────
        if result is None:
            new_status = "DOWN"
            monitor.consecutive_lock_count = 0
            monitor.ultimo_total_locks = 0
            logger.warning(
                f"[DB Monitor] {monitor.nome}: indisponível após {MAX_ATTEMPTS} "
                f"tentativa(s) — {failure_reason}"
            )
        else:
            new_status = _evaluate_locks(monitor, result)

        # ── Persiste ──────────────────────────────────────────────────────────
        status_changed = old_status != new_status
        if status_changed:
            from ..models import EventLog
            event_log = EventLog(
                db_monitor_id=monitor.id,
                old_status=old_status,
                new_status=new_status,
                lock_count=int(monitor.ultimo_total_locks or 0),
            )
            session.add(event_log)

        monitor.status = new_status
        await session.commit()

        # ── Broadcast via WebSocket ────────────────────────────────────────────
        if new_status == "CRITICAL_LOCK":
            priority = "critical"
        elif new_status in ("DOWN", "WARNING"):
            priority = "high"
        else:
            priority = "info"

        # Janela de manutenção ativa cobre esse monitor de DB?
        from .maintenance_service import (
            load_active_window_candidates, is_target_under_maintenance,
        )
        windows = await load_active_window_candidates(session)
        muted_by_maintenance = is_target_under_maintenance(
            windows, db_monitor_id=monitor.id
        )

        await broadcast_callback({
            "type": "db_status_change" if status_changed else "db_status_update",
            "priority": priority,
            "monitor_id": monitor.id,
            "monitor_name": monitor.nome,
            "status": new_status,
            "is_muted": monitor.is_muted,
            "muted_by_maintenance": muted_by_maintenance,
            "ultimo_total_locks": monitor.ultimo_total_locks or 0,
        })

        if status_changed:
            logger.info(f"[DB Monitor] {monitor.nome}: {old_status} → {new_status}")

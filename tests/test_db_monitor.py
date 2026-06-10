"""Testes da lógica de detecção de bloqueios (database_service)."""

from datetime import datetime, timezone, timedelta

from backend.models import DatabaseMonitor
from backend.services.database_service import (
    _classify_response, _count_locks, _row_wait_ms, _is_stale, _evaluate_locks,
)


# ── Classificação da resposta (fail-closed) ───────────────────────────────────

def test_classify_ok():
    ok, info = _classify_response(200, {"rows": [], "error": None})
    assert ok and info == {"rows": [], "error": None}


def test_classify_non_200():
    ok, reason = _classify_response(503, {"rows": []})
    assert not ok and "503" in reason


def test_classify_sentinela_error():
    ok, reason = _classify_response(200, {"rows": [], "error": "Login failed"})
    assert not ok and "Login failed" in reason


def test_classify_missing_rows_is_failure():
    # Mudança de contrato → NÃO assume "sem locks"
    ok, reason = _classify_response(200, {"data": []})
    assert not ok
    ok2, _ = _classify_response(200, None)        # corpo não-JSON
    assert not ok2
    ok3, _ = _classify_response(200, {"rows": "x"})  # rows não-lista
    assert not ok3


# ── Contagem de locks com limiar de waitTime ──────────────────────────────────

def test_count_locks_all():
    rows = [{"waitTime": 100}, {"waitTime": 5000}, {}]
    assert _count_locks(rows, 0) == 3


def test_count_locks_threshold():
    rows = [{"waitTime": 100}, {"waitTime": 5000}, {"waitTime": 9000}]
    assert _count_locks(rows, 3000) == 2   # ignora o de 100ms


def test_row_wait_ms_resilient():
    assert _row_wait_ms({"waitTime": "1234"}) == 1234.0
    assert _row_wait_ms({}) == 0.0
    assert _row_wait_ms({"waitTime": None}) == 0.0


# ── Staleness ─────────────────────────────────────────────────────────────────

def test_is_stale_disabled_by_default():
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert _is_stale(old, 0) is False


def test_is_stale_detects_old_timestamp():
    old = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat().replace("+00:00", "Z")
    assert _is_stale(old, 300) is True


def test_is_stale_fresh_timestamp():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    assert _is_stale(fresh, 300) is False


def test_is_stale_handles_nanoseconds():
    # Formato real da Sentinela: 9 dígitos de fração + Z
    fresh = "2026-05-27T17:48:48.003836736Z"
    # Com tolerância enorme não deve quebrar nem marcar stale por erro de parsing
    assert _is_stale(fresh, 10**9) is False


def test_is_stale_bad_input_never_raises():
    assert _is_stale("não é data", 300) is False
    assert _is_stale(None, 300) is False


# ── Escalada de status (CRITICAL_LOCK imediato → UP) ──────────────────────────

def _monitor():
    return DatabaseMonitor(nome="X", endpoint_url="http://x", status="UP",
                           ultimo_total_locks=0, consecutive_lock_count=0)


def test_locks_go_straight_to_critical():
    m = _monitor()
    # 1ª rodada com locks → CRITICAL_LOCK direto (Sentinela já confirmou 6×)
    assert _evaluate_locks(m, {"rows": [{"waitTime": 5000}]}) == "CRITICAL_LOCK"
    assert m.consecutive_lock_count == 1
    assert m.ultimo_total_locks == 1
    # 2ª rodada com locks → continua CRITICAL_LOCK, contador sobe (informativo)
    assert _evaluate_locks(m, {"rows": [{"waitTime": 5000}, {"waitTime": 6000}]}) == "CRITICAL_LOCK"
    assert m.consecutive_lock_count == 2
    assert m.ultimo_total_locks == 2
    # Sem locks → UP e contadores zerados
    assert _evaluate_locks(m, {"rows": []}) == "UP"
    assert m.consecutive_lock_count == 0
    assert m.ultimo_total_locks == 0


def test_stale_data_without_locks_is_warning(monkeypatch):
    # Sem locks, mas 'updatedAt' obsoleto → WARNING (dados travados na origem).
    # Precisa habilitar a checagem de staleness (desabilitada por padrão).
    monkeypatch.setenv("DB_DATA_MAX_AGE_SECONDS", "300")
    m = _monitor()
    old = "2000-01-01T00:00:00Z"
    assert _evaluate_locks(m, {"rows": [], "updatedAt": old}) == "WARNING"
    assert m.consecutive_lock_count == 0
    assert m.ultimo_total_locks == 0

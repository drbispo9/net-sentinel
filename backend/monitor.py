import asyncio
import html as _html
import logging
import re
import time
import os
import ssl
import warnings
import httpx

# Suppress SSL verification warnings — intentional for a monitoring tool
# that checks availability, not certificate validity.
warnings.filterwarnings("ignore", message=".*Unverified HTTPS.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module="httpx")
from sqlalchemy import select
from .database import AsyncSessionLocal
from .models import Device, DeviceType, DeviceStatus, EventLog, PerformanceLog, DatabaseMonitor
from .services.maintenance_service import (
    load_active_window_candidates,
    is_target_under_maintenance,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP headers / Keyword matching
# ─────────────────────────────────────────────────────────────────────────────

# Headers de navegador real — sem isso, muitos sites (Cloudflare, WAFs, CDNs,
# Wix, GoDaddy, etc.) servem HTML diferente, página de erro ou bloqueio para
# o User-Agent default do httpx ("python-httpx/0.x"), o que quebrava o keyword
# matching em sites perfeitamente acessíveis no navegador.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    # IMPORTANTE: NÃO anunciar brotli (`br`) — o httpx só descomprime gzip e
    # deflate nativamente, então CDNs (Cloudflare etc.) entregavam o corpo em
    # brotli e o keyword matching quebrava (response.text vinha embaralhada).
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_for_match(text: str) -> str:
    """
    Normaliza texto para comparação tolerante:
      - Decodifica entidades HTML (&amp; → &, &nbsp; → espaço, etc).
      - Colapsa qualquer sequência de espaços/quebras/tabs em um único espaço.
      - Tira espaços nas pontas.
      - Converte para caixa baixa (case-insensitive).
    """
    if not text:
        return ""
    decoded = _html.unescape(text)
    collapsed = _WHITESPACE_RE.sub(" ", decoded).strip()
    return collapsed.lower()


def _decode_response_text(response: httpx.Response) -> str:
    """
    Retorna o corpo da resposta como string, robusto contra encodings ruins.

    httpx.Response.text já faz detecção de charset, mas alguns sites mandam
    Content-Type sem charset e/ou meta tags inconsistentes, gerando mojibake
    que esconde o keyword. Aqui caímos para utf-8 com errors='replace' quando
    necessário, garantindo que o texto chegue ao normalizador sem exceções.
    """
    try:
        text = response.text
        if text:
            return text
    except Exception:
        pass
    try:
        return response.content.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _keyword_matches(html_text: str, keyword: str | None) -> bool:
    """
    Verdadeiro se `keyword` está presente em `html_text` de forma tolerante:
      - Espaços/quebras de linha colapsados.
      - Entidades HTML decodificadas.
      - Comparação case-insensitive.

    Keyword vazia/None retorna True (nada a validar).
    """
    if keyword is None:
        return True
    needle = _normalize_for_match(keyword)
    if not needle:
        return True
    haystack = _normalize_for_match(html_text)
    return needle in haystack


# ─────────────────────────────────────────────────────────────────────────────
# L7 Timing helpers using httpx trace hooks
# ─────────────────────────────────────────────────────────────────────────────

def _build_trace_callback(timings: dict):
    """
    Returns an async trace callback compatible with httpx/httpcore event hooks.
    Uses the actual event names emitted by httpcore (verified against 0.28.x):
      - connection.connect_tcp.started   → DNS + TCP resolution begins
      - connection.connect_tcp.complete  → TCP connected (DNS+TCP done)
      - connection.start_tls.started     → TLS handshake begins
      - connection.start_tls.complete    → TLS handshake done
      - http11.send_request_headers.started   → request sent
      - http11.receive_response_headers.complete → first byte received (TTFB)
      - http11.receive_response_body.complete    → body download complete
    """
    async def on_event(event_name: str, info: dict):
        now = time.monotonic()
        if event_name == "connection.connect_tcp.started":
            timings["dns_tcp_start"] = now       # DNS resolution + TCP connect begins
        elif event_name == "connection.connect_tcp.complete":
            timings["tcp_done"] = now             # TCP connected (DNS+TCP finished)
        elif event_name == "connection.start_tls.started":
            timings["tls_start"] = now
        elif event_name == "connection.start_tls.complete":
            timings["tls_done"] = now             # TLS handshake complete
        elif event_name in (
            "http11.send_request_headers.started",
            "http2.send_request_headers.started",
        ):
            timings["req_start"] = now
        elif event_name in (
            "http11.receive_response_headers.complete",
            "http2.receive_response_headers.complete",
        ):
            timings["ttfb"] = now                 # first byte
        elif event_name in (
            "http11.receive_response_body.complete",
            "http2.receive_response_body.complete",
        ):
            timings["body_done"] = now            # download complete

    return on_event


def _compute_l7_metrics(timings: dict, total_elapsed_ms: float) -> dict:
    """
    Convert raw monotonic timestamps into millisecond segments.
    Falls back gracefully when individual hook timestamps are missing.
    """
    def ms(a, b):
        """Returns (b - a) * 1000 rounded to 1 decimal, or None if missing."""
        if a is not None and b is not None and b > a:
            return round((b - a) * 1000, 1)
        return None

    dns_tcp_start = timings.get("dns_tcp_start")
    tcp_done      = timings.get("tcp_done")
    tls_start     = timings.get("tls_start")
    tls_done      = timings.get("tls_done")
    ttfb          = timings.get("ttfb")
    body_done     = timings.get("body_done")

    # DNS + TCP combined: from connect_tcp.started to connect_tcp.complete
    # We report this as "dns_ms" since it includes DNS resolution time.
    # (httpcore does not expose DNS-only separately; TCP connect follows immediately)
    dns_ms = ms(dns_tcp_start, tcp_done)

    # TLS handshake: from start_tls.started to start_tls.complete
    if tls_start is not None and tls_done is not None:
        ssl_ms     = ms(tls_start, tls_done)
        connect_ms = ms(tcp_done, tls_start)   # gap between TCP done and TLS start
    elif tcp_done is not None and tls_done is not None:
        ssl_ms     = ms(tcp_done, tls_done)
        connect_ms = None
    else:
        ssl_ms     = None
        connect_ms = None

    # TTFB: from request sent (or tls_done / tcp_done) to first byte
    req_start = timings.get("req_start") or tls_done or tcp_done
    ttfb_ms   = ms(req_start, ttfb)

    # Download: TTFB → body complete
    download_ms = ms(ttfb, body_done)

    return {
        "dns_ms":      dns_ms,
        "connect_ms":  connect_ms,
        "ssl_ms":      ssl_ms,
        "ttfb_ms":     ttfb_ms,
        "download_ms": download_ms,
        "total_ms":    round(total_elapsed_ms, 1),
    }


def _is_ssl_error(exc: BaseException) -> bool:
    """
    Detecta se a exceção foi causada por um problema de certificado SSL
    (expirado, hostname inválido, cadeia não confiável, etc).

    httpx encapsula o erro em ConnectError, então percorremos a cadeia de
    causas procurando por ssl.SSLError / ssl.SSLCertVerificationError.
    """
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, ssl.SSLError):
            return True
        e = e.__cause__ or e.__context__

    # Fallback por mensagem (cobre wrappers que não preservam a causa).
    msg = str(exc).lower()
    return "certificate verify failed" in msg or "ssl: " in msg or "sslerror" in msg


# ─────────────────────────────────────────────────────────────────────────────
# MonitorManager
# ─────────────────────────────────────────────────────────────────────────────

class MonitorManager:
    def __init__(self, websocket_broadcast_callback):
        self.broadcast = websocket_broadcast_callback
        self.running = False

    # ── WEB ──────────────────────────────────────────────────────────────────

    async def _check_web_device(self, device_id: int):
        """Check a single web device, collect L7 timings, and persist results.

        Each call creates its own httpx.AsyncClient with keepalive disabled so
        the TCP connection is always fresh and connect_tcp trace events always fire,
        giving us accurate DNS+TCP timing on every check.

        Two clients são criados:
          - secure (verify=True): usado primeiro; detecta certificado inválido.
          - insecure (verify=False): fallback quando o certificado falha, para
            confirmar que o site está *disponível* mesmo com cert problemático
            (nesse caso o status vira WARNING, não DOWN).
        """
        # Fresh clients per check — no connection reuse, guarantees trace hooks fire.
        # `headers=_BROWSER_HEADERS` é crítico para keyword matching: muitos sites
        # respondem com HTML diferente (ou bloqueio) ao User-Agent default do httpx.
        limits = httpx.Limits(
            max_connections=1,
            max_keepalive_connections=0,
        )
        async with httpx.AsyncClient(limits=limits, verify=True,
                                     headers=_BROWSER_HEADERS) as secure_client, \
                   httpx.AsyncClient(limits=limits, verify=False,
                                     headers=_BROWSER_HEADERS) as insecure_client:
            await self._run_web_check(device_id, secure_client, insecure_client)

    async def _run_oab_authenticated_check(self, device, client: httpx.AsyncClient, timeout: httpx.Timeout):
        """Runs the custom OAB authenticated L7 check and returns (response_time_ms, l7_metrics) or raises Exception."""
        auth_url = os.getenv("PORTAL_OAB_AUTH_URL", "https://appws.oabgo.org.br/wsapp/wsapp/authenticate")
        username = os.getenv("PORTAL_OAB_USERNAME")
        password = os.getenv("PORTAL_OAB_PASSWORD")

        if not username or not password:
            logger.warning("[Monitor] OAB check requested but PORTAL_OAB_USERNAME or PORTAL_OAB_PASSWORD is not set in .env")
            raise ValueError("Credenciais do Portal OAB ausentes no arquivo .env")

        # 1. POST Authentication (support both traditional login/senha and username/password)
        login_payload = {
            "login": username,
            "senha": password,
            "username": username,
            "password": password
        }

        # POST without trace hooks to avoid polluting the L7 timing for the validation URL
        login_resp = await client.post(auth_url, json=login_payload, timeout=timeout)
        if login_resp.status_code != 200:
            raise ValueError(f"Autenticação falhou com status HTTP {login_resp.status_code}")

        # Check if the API returned a token (in case it uses token auth instead of standard cookies)
        headers = {}
        try:
            data = login_resp.json()
            token = data.get("token") or data.get("accessToken") or data.get("access_token") or data.get("jwt")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        except Exception:
            pass

        # 2. GET Protected Validation URL (device.address) with L7 Trace Hooks
        timings: dict = {}
        start = time.monotonic()
        response = await client.get(
            device.address,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
            extensions={"trace": _build_trace_callback(timings)},
        )
        elapsed_ms = (time.monotonic() - start) * 1000

        # Validate response: must be 200 OK.
        if response.status_code != 200:
            raise ValueError(f"Validação da página interna falhou com status HTTP {response.status_code}")

        # Keyword Matching também se aplica ao fluxo autenticado.
        keyword = (device.texto_obrigatorio or "").strip()
        if device.validar_texto and keyword:
            body = _decode_response_text(response)
            if not _keyword_matches(body, keyword):
                raise ValueError(
                    f"Validação de conteúdo falhou: '{keyword}' ausente na resposta autenticada"
                )

        response_time_ms = round(elapsed_ms)
        l7_metrics = _compute_l7_metrics(timings, elapsed_ms)

        # Force a fallback total_ms if computed timing helper missed some fields
        if l7_metrics and l7_metrics.get("total_ms") is None:
            l7_metrics["total_ms"] = round(elapsed_ms, 1)

        return response_time_ms, l7_metrics

    async def _run_web_check(self, device_id: int, secure_client: httpx.AsyncClient,
                             insecure_client: httpx.AsyncClient):
        """Inner implementation — runs the actual HTTP check and persists L7 metrics."""
        async with AsyncSessionLocal() as session:
            device = await session.get(Device, device_id)
            if not device:
                return

            failures = 0
            max_attempts = 3
            timeout = httpx.Timeout(10.0)
            response_time_ms = None
            l7_metrics = None
            ssl_invalid = False  # site disponível, mas certificado SSL problemático

            for attempt in range(1, max_attempts + 1):
                try:
                    if device.slug_identificador == "portal_oab":
                        # Check interno autenticado — usa o cliente não-verificado.
                        response_time_ms, l7_metrics = await self._run_oab_authenticated_check(device, insecure_client, timeout)
                        failures = 0
                        logger.debug(f"[Monitor] {device.name} (OAB Authenticated) check succeeded.")
                        break
                    else:
                        timings: dict = {}
                        ssl_invalid_attempt = False

                        # 1ª tentativa: COM verificação de certificado.
                        start = time.monotonic()
                        try:
                            response = await secure_client.get(
                                device.address,
                                timeout=timeout,
                                follow_redirects=True,
                                extensions={"trace": _build_trace_callback(timings)},
                            )
                            elapsed_ms = (time.monotonic() - start) * 1000
                        except Exception as exc:
                            # Certificado inválido? Confirma disponibilidade ignorando SSL.
                            if _is_ssl_error(exc):
                                logger.warning(
                                    f"[Monitor] {device.name} — Certificado SSL inválido/expirado. "
                                    f"Verificando disponibilidade sem validar o certificado."
                                )
                                ssl_invalid_attempt = True
                                timings = {}
                                start = time.monotonic()
                                response = await insecure_client.get(
                                    device.address,
                                    timeout=timeout,
                                    follow_redirects=True,
                                    extensions={"trace": _build_trace_callback(timings)},
                                )
                                elapsed_ms = (time.monotonic() - start) * 1000
                            else:
                                raise  # erro não-SSL → tratado como falha abaixo

                        if 200 <= response.status_code < 400:
                            # ── Keyword Matching (Content Validation) ──────
                            # Falha de keyword conta como UMA falha — retentamos
                            # como qualquer outro erro transitório (render lento,
                            # SPA hidratando, conteúdo dinâmico) antes de derrubar.
                            keyword = (device.texto_obrigatorio or "").strip()
                            if device.validar_texto and keyword:
                                body = _decode_response_text(response)
                                if not _keyword_matches(body, keyword):
                                    logger.warning(
                                        f"[Monitor] {device.name} — Keyword '{keyword}' "
                                        f"ausente no HTML (tentativa {attempt}/{max_attempts}). "
                                        f"Status HTTP {response.status_code}, tamanho={len(body)}b."
                                    )
                                    failures += 1
                                    # cai para o sleep+retry abaixo
                                else:
                                    failures = 0
                                    ssl_invalid = ssl_invalid_attempt
                                    response_time_ms = round(elapsed_ms)
                                    l7_metrics = _compute_l7_metrics(timings, elapsed_ms)
                                    logger.debug(
                                        f"[Monitor] {device.name} keyword OK + L7 timings: "
                                        f"dns={l7_metrics.get('dns_ms')}ms "
                                        f"ssl={l7_metrics.get('ssl_ms')}ms "
                                        f"ttfb={l7_metrics.get('ttfb_ms')}ms "
                                        f"dl={l7_metrics.get('download_ms')}ms "
                                        f"total={l7_metrics.get('total_ms')}ms"
                                    )
                                    break
                            else:
                                failures = 0
                                ssl_invalid = ssl_invalid_attempt
                                response_time_ms = round(elapsed_ms)
                                l7_metrics = _compute_l7_metrics(timings, elapsed_ms)
                                logger.debug(
                                    f"[Monitor] {device.name} L7 timings: "
                                    f"dns={l7_metrics.get('dns_ms')}ms "
                                    f"ssl={l7_metrics.get('ssl_ms')}ms "
                                    f"ttfb={l7_metrics.get('ttfb_ms')}ms "
                                    f"dl={l7_metrics.get('download_ms')}ms "
                                    f"total={l7_metrics.get('total_ms')}ms"
                                )
                                break
                        else:
                            failures += 1

                except Exception as exc:
                    logger.debug(f"[Monitor] {device.name} attempt {attempt} failed: {exc}")
                    failures += 1

                if failures < max_attempts:
                    await asyncio.sleep(5)

            # ── Determine status ──────────────────────────────────────────────
            if failures >= max_attempts:
                new_status = DeviceStatus.DOWN
            elif ssl_invalid:
                new_status = DeviceStatus.WARNING  # disponível, mas certificado inválido
            else:
                new_status = DeviceStatus.UP
            if new_status == DeviceStatus.DOWN:
                response_time_ms = None
                l7_metrics = None

            device.failure_count = failures
            device.response_time_ms = response_time_ms
            device.dns_ms = l7_metrics.get("dns_ms") if l7_metrics else None

            # ── Persist L7 performance log ────────────────────────────────────
            if l7_metrics is not None:
                perf_log = PerformanceLog(
                    device_id=device.id,
                    **l7_metrics,
                )
                session.add(perf_log)

            # ── Handle status change ──────────────────────────────────────────
            if new_status != device.status:
                old_status = device.status
                device.status = new_status

                event_log = EventLog(
                    device_id=device.id,
                    old_status=old_status,
                    new_status=new_status,
                    latency=response_time_ms,
                )
                session.add(event_log)
                await session.commit()

                # Janela de manutenção ativa? Suprime som/popup/notificação,
                # mas o status muda visualmente como sempre.
                windows = await load_active_window_candidates(session)
                muted_by_maintenance = is_target_under_maintenance(
                    windows, device_id=device.id
                )

                logger.info(
                    f"[Monitor] {device.name}: {old_status.value} → {new_status.value}"
                    + (" [maintenance window]" if muted_by_maintenance else "")
                )

                # Status-change alert broadcast (high priority — carries status info)
                await self.broadcast({
                    "type": "status_change",
                    "priority": "high" if new_status == DeviceStatus.DOWN else "info",
                    "device_id": device.id,
                    "device_name": device.name,
                    "status": new_status.value,
                    "is_muted": device.is_muted,
                    "muted_by_maintenance": muted_by_maintenance,
                    "response_time_ms": response_time_ms,
                    "dns_ms": device.dns_ms,
                })
            else:
                await session.commit()

                # Lightweight heartbeat broadcast — only what the dashboard card needs
                await self.broadcast({
                    "type": "status_update",
                    "device_id": device.id,
                    "status": device.status.value,
                    "response_time_ms": response_time_ms,
                    "dns_ms": device.dns_ms,
                })

    async def monitor_web_loop(self):
        """
        Main web monitoring loop.
        Reloads device IDs from the DB on every iteration so newly registered
        sites are picked up automatically without a server restart.
        """
        logger.info("[Monitor] Web loop started")
        while self.running:
            try:
                # ── Reload device list on every cycle (dynamic registration) ──
                async with AsyncSessionLocal() as session:
                    result = await session.execute(
                        select(Device.id).where(Device.device_type == DeviceType.WEB)
                    )
                    device_ids = [row[0] for row in result.all()]

                logger.debug(f"[Monitor] Web cycle — checking {len(device_ids)} device(s)")

                if device_ids:
                    # Each device gets its own isolated client (created inside _check_web_device)
                    tasks = [self._check_web_device(did) for did in device_ids]
                    await asyncio.gather(*tasks, return_exceptions=True)

            except Exception as e:
                logger.error(f"[Monitor] Web loop error: {e}")

            await asyncio.sleep(30)

    # ── HARDWARE ──────────────────────────────────────────────────────────────

    async def _check_hardware_device(self, device_id: int):
        import platform
        from .services.snmp_service import get_snmp_cpu

        async with AsyncSessionLocal() as session:
            device = await session.get(Device, device_id)
            if not device:
                return

            failures = 0
            max_attempts = 1  # Fast status check
            cpu_usage = None
            is_up = False

            if not device.comunidade_snmp or not device.oid_cpu:
                # Fallback to ICMP ping when SNMP is not configured
                param_count   = '-n' if platform.system().lower() == 'windows' else '-c'
                param_timeout = '-w' if platform.system().lower() == 'windows' else '-W'
                timeout_val   = '500' if platform.system().lower() == 'windows' else '1'

                for attempt in range(1, max_attempts + 1):
                    try:
                        import subprocess
                        loop = asyncio.get_running_loop()

                        def run_ping():
                            return subprocess.run(
                                ['ping', param_count, '1', param_timeout, timeout_val, device.address],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            ).returncode == 0

                        is_up_now = await loop.run_in_executor(None, run_ping)

                        if is_up_now:
                            is_up = True
                            failures = 0
                            break
                        else:
                            failures += 1
                    except Exception as e:
                        logger.error(f"[Monitor] Ping Error on {device.name}: {repr(e)}")
                        failures += 1

                    if failures < max_attempts:
                        await asyncio.sleep(1)  # Faster retry

                new_status = DeviceStatus.DOWN if failures >= max_attempts else DeviceStatus.UP

            else:
                for attempt in range(1, max_attempts + 1):
                    try:
                        cpu_usage = await get_snmp_cpu(
                            ip=device.address,
                            community=device.comunidade_snmp,
                            oid_str=device.oid_cpu,
                            version=device.versao_snmp or 'v2c',
                        )
                        is_up = True
                        failures = 0
                        break
                    except TimeoutError:
                        failures += 1
                    except Exception as e:
                        logger.error(f"[Monitor] SNMP Error on {device.name}: {e}")
                        failures += 1

                    if failures < max_attempts:
                        await asyncio.sleep(1)  # Faster retry

                new_status = DeviceStatus.DOWN if failures >= max_attempts else DeviceStatus.UP

                if is_up and cpu_usage is not None:
                    if cpu_usage > 90:
                        if device.ultimo_uso_cpu is not None and device.ultimo_uso_cpu > 90:
                            new_status = DeviceStatus.CRITICAL_OVERLOAD
                    else:
                        new_status = DeviceStatus.UP

                    device.ultimo_uso_cpu = cpu_usage

            device.failure_count = failures
            device.response_time_ms = None

            if new_status != device.status:
                old_status = device.status
                device.status = new_status

                log = EventLog(
                    device_id=device.id,
                    old_status=old_status,
                    new_status=new_status,
                    latency=None,
                )
                session.add(log)
                await session.commit()

                windows = await load_active_window_candidates(session)
                muted_by_maintenance = is_target_under_maintenance(
                    windows, device_id=device.id
                )

                logger.info(
                    f"[Monitor] {device.name}: {old_status.value} → {new_status.value}"
                    + (" [maintenance window]" if muted_by_maintenance else "")
                )

                await self.broadcast({
                    "type": "status_change",
                    "priority": "critical" if new_status == DeviceStatus.CRITICAL_OVERLOAD else (
                        "high" if new_status == DeviceStatus.DOWN else "info"
                    ),
                    "device_id": device.id,
                    "device_name": device.name,
                    "status": new_status.value,
                    "is_muted": device.is_muted,
                    "muted_by_maintenance": muted_by_maintenance,
                })
            else:
                await session.commit()
                await self.broadcast({
                    "type": "status_update",
                    "device_id": device.id,
                    "status": device.status.value,
                    "response_time_ms": None,
                })

    async def monitor_hardware_loop(self):
        """
        Hardware monitoring loop.
        Reloads device IDs from the DB on every iteration — same dynamic
        registration pattern as the web loop.
        """
        logger.info("[Monitor] Hardware loop started")
        while self.running:
            try:
                # ── Reload hardware device list dynamically ────────────────────
                async with AsyncSessionLocal() as session:
                    result = await session.execute(
                        select(Device.id).where(Device.device_type == DeviceType.HARDWARE)
                    )
                    device_ids = [row[0] for row in result.all()]

                logger.debug(f"[Monitor] Hardware cycle — checking {len(device_ids)} device(s)")

                if device_ids:
                    tasks = [self._check_hardware_device(did) for did in device_ids]
                    await asyncio.gather(*tasks, return_exceptions=True)

            except Exception as e:
                logger.error(f"[Monitor] Hardware loop error: {e}")

            await asyncio.sleep(3)

    async def monitor_database_loop(self):
        # Intervalo configurável — locks são sensíveis ao tempo, então o padrão
        # (30s) é mais agressivo que os 60s antigos. Ajuste via env se necessário.
        interval = max(5, int(os.getenv("DB_MONITOR_INTERVAL_SECONDS", "30")))
        logger.info(f"[Monitor] Database loop started (intervalo: {interval}s)")
        while self.running:
            try:
                from .services.database_service import check_database_lock_monitor

                # Reload monitor list dynamically each cycle
                async with AsyncSessionLocal() as session:
                    result = await session.execute(select(DatabaseMonitor.id))
                    monitor_ids = [row[0] for row in result.all()]

                logger.debug(
                    f"[Monitor] Database cycle — checking {len(monitor_ids)} monitor(s)"
                )

                if monitor_ids:
                    tasks = [
                        check_database_lock_monitor(mid, self.broadcast)
                        for mid in monitor_ids
                    ]
                    await asyncio.gather(*tasks, return_exceptions=True)

            except Exception as e:
                logger.error(f"[Monitor] Database loop error: {e}")

            await asyncio.sleep(interval)

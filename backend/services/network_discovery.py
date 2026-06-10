"""
backend/services/network_discovery.py
─────────────────────────────────────
Descoberta de equipamentos de rede (switches, roteadores, firewalls e access
points) num range IPv4 da rede local.

Filtros em três camadas — só passam hosts que satisfaçam ALGUMA delas:
  1) OUI conhecido de fabricante de rede.
  2) Porta de management aberta (SNMP 161, Telnet 23, NETCONF 830).
  3) Banner em 22/23/80 com keyword reconhecida.

Fabricantes "de mesa" (Apple, Dell, HP PC, Epson, Canon, Samsung, Brother)
são rejeitados imediatamente pelo OUI, mesmo que tenham 22 ou 80 abertos.

Implementação totalmente assíncrona; a concorrência é limitada por um
asyncio.Semaphore. Sem dependências externas — usa apenas stdlib + asyncio.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import platform
import re
import socket
from typing import Any

logger = logging.getLogger(__name__)


# ─── OUI / Vendor tables ────────────────────────────────────────────────────
# Chaves: 3 primeiros octetos do MAC, sem separadores, em caixa alta.

NETWORK_DEVICE_OUI: dict[str, str] = {
    # ── Cisco ────────────────────────────────────────────────────────────
    "00000C": "Cisco", "001007": "Cisco", "0014A9": "Cisco", "0017DF": "Cisco",
    "001A2F": "Cisco", "001AA1": "Cisco", "001B0D": "Cisco", "001C0F": "Cisco",
    "001E14": "Cisco", "001E7A": "Cisco", "001F6C": "Cisco", "00216A": "Cisco",
    "0022BD": "Cisco", "0023AB": "Cisco", "0024C4": "Cisco", "0025B4": "Cisco",
    "002698": "Cisco", "00272E": "Cisco", "1C1D86": "Cisco", "44D3CA": "Cisco",
    "586D8F": "Cisco", "6CFA89": "Cisco", "C4641F": "Cisco", "F84F57": "Cisco",

    # ── Mikrotik ─────────────────────────────────────────────────────────
    "000C42": "Mikrotik", "4C5E0C": "Mikrotik", "64D154": "Mikrotik",
    "6C3B6B": "Mikrotik", "B869F4": "Mikrotik", "C4AD34": "Mikrotik",
    "CC2DE0": "Mikrotik", "D4CA6D": "Mikrotik", "DCA6CC": "Mikrotik",
    "E48D8C": "Mikrotik", "F4F2FF": "Mikrotik", "08555D": "Mikrotik",

    # ── TP-Link ──────────────────────────────────────────────────────────
    "000AEB": "TP-Link", "002127": "TP-Link", "10FE56": "TP-Link",
    "14CC20": "TP-Link", "1C61B4": "TP-Link", "1CFA68": "TP-Link",
    "302303": "TP-Link", "50C7BF": "TP-Link", "60E327": "TP-Link",
    "744D28": "TP-Link", "A42BB0": "TP-Link", "B0487A": "TP-Link",
    "C46E1F": "TP-Link", "EC086B": "TP-Link", "F4F26D": "TP-Link",

    # ── Ubiquiti ─────────────────────────────────────────────────────────
    "0418D6": "Ubiquiti", "183BD2": "Ubiquiti", "24A43C": "Ubiquiti",
    "44D9E7": "Ubiquiti", "688F84": "Ubiquiti", "788A20": "Ubiquiti",
    "802AA8": "Ubiquiti", "DC9FDB": "Ubiquiti", "F09FC2": "Ubiquiti",
    "FC9F2B": "Ubiquiti", "B4FBE4": "Ubiquiti", "E063DA": "Ubiquiti",

    # ── Huawei ───────────────────────────────────────────────────────────
    "001E10": "Huawei", "002568": "Huawei", "00259E": "Huawei",
    "240995": "Huawei", "486276": "Huawei", "7C1135": "Huawei",
    "7C11BE": "Huawei", "8038BC": "Huawei", "8C34FD": "Huawei",
    "B4CD27": "Huawei", "C46AB7": "Huawei", "F898B9": "Huawei",

    # ── Netgear ──────────────────────────────────────────────────────────
    "00095B": "Netgear", "000FB5": "Netgear", "001B2F": "Netgear",
    "001E2A": "Netgear", "0024B2": "Netgear", "008EF2": "Netgear",
    "200CC8": "Netgear", "28C68E": "Netgear", "446D57": "Netgear",
    "6CCDD6": "Netgear", "9C3DCF": "Netgear", "B0B98A": "Netgear",
    "C03F0E": "Netgear", "E091F5": "Netgear", "A040A0": "Netgear",

    # ── D-Link ───────────────────────────────────────────────────────────
    "00055D": "D-Link", "000D88": "D-Link", "000F3D": "D-Link",
    "001195": "D-Link", "00179A": "D-Link", "001CF0": "D-Link",
    "00226B": "D-Link", "0024A5": "D-Link", "00265A": "D-Link",
    "1C7EE5": "D-Link", "5CD998": "D-Link", "84C9B2": "D-Link",
    "9094E4": "D-Link", "BC0F9A": "D-Link", "CCB255": "D-Link",

    # ── Juniper ──────────────────────────────────────────────────────────
    "000585": "Juniper", "001DB5": "Juniper", "001F12": "Juniper",
    "0024DC": "Juniper", "288A1C": "Juniper", "30B64F": "Juniper",
    "40A677": "Juniper", "5C5EAB": "Juniper", "78FE3D": "Juniper",
    "B0A86E": "Juniper", "EC3EF7": "Juniper",

    # ── HP Networking (ProCurve) ─────────────────────────────────────────
    "000802": "HP Networking", "001A6B": "HP Networking",
    "001CC4": "HP Networking", "00237D": "HP Networking",
    "0024A8": "HP Networking", "002655": "HP Networking",
    "10604B": "HP Networking", "3C4A92": "HP Networking",
    "5C8A38": "HP Networking", "705A0F": "HP Networking",
    "9457A5": "HP Networking", "B4B52F": "HP Networking",

    # ── Aruba ────────────────────────────────────────────────────────────
    "000B86": "Aruba", "24DEC6": "Aruba", "6CF37F": "Aruba",
    "9020C2": "Aruba", "94B40F": "Aruba", "A87E33": "Aruba",
    "ACA31E": "Aruba", "B4C293": "Aruba", "D8C7C8": "Aruba",
    "F021E5": "Aruba",

    # ── Outras marcas de equipamento de rede ─────────────────────────────
    "0007E9": "Fortinet", "00090F": "Fortinet", "0894EF": "Fortinet",
    "70B3D5": "Fortinet", "9017AC": "Fortinet", "E84E06": "Fortinet",
    "001408": "Netgate (pfSense)",
    "00057B": "Zyxel", "001349": "Zyxel", "5C6A80": "Zyxel",
}

EXCLUDE_OUI: set[str] = {
    # ── Apple ────────────────────────────────────────────────────────────
    "000393", "000A27", "000A95", "000D93", "001451", "00163E", "001B63",
    "001CB3", "001D4F", "001EC2", "001F5B", "002241", "002332", "0023DF",
    "002500", "00264A", "002608", "147DDA", "186590", "1C9148", "20768F",
    "247702", "24F094", "28E14C", "3CAB8E", "3CD0F8", "404D7F", "5404A6",
    "60FACD", "70CD60", "78FD94", "7CC3A1", "9810E8", "98D6BB", "ACDE48",
    "B065BD", "B8782E", "B888E3", "BC3BAF", "BC52B7", "C0F2FB", "D023DB",
    "D02598", "D89695", "DCA904", "DC2B61", "E0F5C6", "F0DBE2",

    # ── Dell ─────────────────────────────────────────────────────────────
    "001422", "001D09", "002219", "002564", "00248C", "001855", "0026B9",
    "1866DA", "18A99B", "246E96", "3417EB", "5CF9DD", "78AC44", "8870EE",
    "98E743", "A41F72", "B083FE", "B8AC6F", "B8CA3A", "C81F66", "D0436D",
    "D481D7", "DC4A3E", "ECF4BB", "F8B156",

    # ── HP / Compaq PCs (não networking) ─────────────────────────────────
    "0010E0", "001321", "0015F2", "0017A4", "00185F", "001C25",
    "001E68", "0025B3", "0030C1", "0080C8", "0080CA", "0080CF",
    "082E5F", "1062E5", "147517", "2014D7", "245EBE", "2C44FD",
    "30FD11", "344B3D", "38F9D3", "508F4C", "5C879C", "70BEB5",
    "806E6F", "8C0F6F", "9C8E99",

    # ── Epson ────────────────────────────────────────────────────────────
    "000048", "000216", "002066", "44D884", "64EB8C", "84C0EF",
    "9CAE03", "A4EE57", "AC9AB4", "F8D027",

    # ── Canon ────────────────────────────────────────────────────────────
    "000085", "0000FA", "00146C", "001E8F", "0026FA", "30307C",
    "30D17C", "5829A2", "68B8DC", "846DCF", "98C8D6", "A4F05E",
    "B41488", "D80CC9", "E80462", "F432E9",

    # ── Samsung ──────────────────────────────────────────────────────────
    "0000F0", "000918", "00126E", "001247", "0015B9", "00166B",
    "001731", "001839", "0018AF", "001A8A", "001D25", "00214C",
    "00265D", "002566", "001143", "1C5A3E", "20D390", "346BD3",
    "388B59", "3C5A37", "4441EA", "5CF6DC", "8030DC", "88329B",
    "98F170", "C8190F", "F08F23",

    # ── Brother (impressoras) ────────────────────────────────────────────
    "001BA9", "008092", "30055C", "3C2AF4", "4C0BBE", "6CD4FE",
    "B07994", "C8D9D2",
}

COMMON_PORTS: dict[int, str] = {
    22:  "SSH",
    23:  "Telnet",
    80:  "HTTP",
    161: "SNMP",
    443: "HTTPS",
    830: "NETCONF",
}

NETWORK_KEYWORDS: list[str] = [
    "cisco", "mikrotik", "routeros", "ubiquiti", "edgeos",
    "juniper", "junos", "hp procurve", "aruba", "huawei",
    "d-link", "tp-link", "netgear", "switch", "router",
    "firewall", "fortigate", "pfsense", "openwrt",
]

# Portas usadas para detectar "host vivo" rapidamente via TCP handshake.
_LIVENESS_PORTS = (80, 443, 22, 23, 161)

# Tamanho máximo permitido do range — barreira de segurança para não
# permitir que alguém dispare uma varredura de /16 acidentalmente.
_MAX_HOSTS_PER_SCAN = 4096

_MAC_RE = re.compile(r"([0-9a-fA-F]{2}(?:[:\-][0-9a-fA-F]{2}){5})")


def _is_windows() -> bool:
    return platform.system().lower().startswith("win")


def _normalize_mac(mac: str) -> str:
    return mac.upper().replace("-", ":")


def _oui_from_mac(mac: str) -> str:
    """'AA:BB:CC:DD:EE:FF' → 'AABBCC'."""
    return mac.replace(":", "").replace("-", "").upper()[:6]


# ─── Async network primitives ───────────────────────────────────────────────

async def _check_tcp_port(ip: str, port: int, timeout: float = 0.5) -> bool:
    """True se o handshake TCP completar dentro do timeout."""
    try:
        fut = asyncio.open_connection(ip, port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def get_mac(ip: str) -> str | None:
    """
    Dispara um ping para alimentar a ARP cache do SO e em seguida lê a
    saída do `arp` para extrair o MAC do `ip`. Retorna None em qualquer
    falha (host fora do segmento L2, arp ausente, etc).
    """
    is_win = _is_windows()
    ping_cmd = (
        ["ping", "-n", "1", "-w", "500", ip] if is_win
        else ["ping", "-c", "1", "-W", "1", ip]
    )
    arp_cmd = (
        ["arp", "-a", ip] if is_win
        else ["arp", "-n", ip]
    )

    try:
        ping_proc = await asyncio.create_subprocess_exec(
            *ping_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(ping_proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            try:
                ping_proc.kill()
            except Exception:
                pass

        arp_proc = await asyncio.create_subprocess_exec(
            *arp_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(arp_proc.communicate(), timeout=2.0)
        text = stdout.decode(errors="ignore")

        for line in text.splitlines():
            if ip not in line:
                continue
            m = _MAC_RE.search(line)
            if not m:
                continue
            mac = _normalize_mac(m.group(1))
            if mac in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF"):
                continue
            return mac
    except Exception as e:
        logger.debug("[NetScan] get_mac(%s) falhou: %s", ip, e)

    return None


async def scan_ports(ip: str, timeout: float = 0.5) -> list[dict]:
    """
    Faz varredura TCP em `COMMON_PORTS` em paralelo.
    Retorna lista de {"port": int, "service": str} apenas das abertas.
    """
    ports = list(COMMON_PORTS.items())
    results = await asyncio.gather(
        *(_check_tcp_port(ip, p, timeout) for p, _ in ports),
        return_exceptions=True,
    )
    out: list[dict] = []
    for (port, service), ok in zip(ports, results):
        if ok is True:
            out.append({"port": port, "service": service})
    return out


async def grab_banner(ip: str, port: int) -> str | None:
    """
    Abre TCP em (ip, port), envia um probe básico para portas HTTP e lê
    até 256 bytes da resposta. Timeout total de 2s. None em falha.
    """
    async def _do() -> str:
        reader, writer = await asyncio.open_connection(ip, port)
        try:
            if port in (80, 443):
                writer.write(
                    f"GET / HTTP/1.0\r\nHost: {ip}\r\n"
                    f"User-Agent: NetSentinel-Discovery\r\n\r\n".encode()
                )
                await writer.drain()
            data = await reader.read(256)
            return data.decode("utf-8", errors="ignore")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    try:
        return await asyncio.wait_for(_do(), timeout=2.0)
    except Exception:
        return None


def _detect_type_from_banner(banner_lower: str) -> str | None:
    """Mapeia conteúdo do banner para um device_type conhecido."""
    if (
        "firewall" in banner_lower
        or "fortigate" in banner_lower
        or "fortinet" in banner_lower
        or "pfsense" in banner_lower
    ):
        return "firewall"
    if "switch" in banner_lower or "procurve" in banner_lower:
        return "switch"
    if "routeros" in banner_lower or "router" in banner_lower:
        return "router"
    if (
        "access point" in banner_lower
        or "accesspoint" in banner_lower
        or "wifi ap" in banner_lower
        or " ap " in f" {banner_lower} "
    ):
        return "access_point"
    return None


def _vendor_from_banner(banner_lower: str) -> str | None:
    for kw, name in (
        ("cisco",    "Cisco"),
        ("mikrotik", "Mikrotik"), ("routeros", "Mikrotik"),
        ("ubiquiti", "Ubiquiti"), ("edgeos",   "Ubiquiti"),
        ("juniper",  "Juniper"),  ("junos",    "Juniper"),
        ("aruba",    "Aruba"),    ("procurve", "HP Networking"),
        ("huawei",   "Huawei"),
        ("d-link",   "D-Link"),
        ("tp-link",  "TP-Link"),
        ("netgear",  "Netgear"),
        ("fortigate", "Fortinet"), ("fortinet", "Fortinet"),
        ("pfsense",  "pfSense"),
        ("openwrt",  "OpenWrt"),
    ):
        if kw in banner_lower:
            return name
    return None


async def classify_device(
    ip: str, mac: str | None, open_ports: list[dict]
) -> dict[str, Any] | None:
    """
    Aplica os três filtros do spec e devolve o dict do dispositivo se ele
    for um equipamento de rede; None caso contrário.
    """
    port_nums = {p["port"] for p in open_ports}
    vendor: str | None = None
    is_network_device = False
    device_type = "unknown"

    # ── 1) OUI filter ────────────────────────────────────────────────────
    if mac:
        oui = _oui_from_mac(mac)
        if oui in EXCLUDE_OUI:
            return None
        if oui in NETWORK_DEVICE_OUI:
            vendor = NETWORK_DEVICE_OUI[oui]
            is_network_device = True

    # ── 2) Management ports ──────────────────────────────────────────────
    if not is_network_device and (port_nums & {23, 161, 830}):
        is_network_device = True

    # ── 3) Banner grabbing (sempre que houver porta 22/23/80) ────────────
    banners_text = ""
    banner_targets = [p for p in (23, 22, 80) if p in port_nums]
    if banner_targets:
        banner_results = await asyncio.gather(
            *(grab_banner(ip, p) for p in banner_targets),
            return_exceptions=True,
        )
        for b in banner_results:
            if isinstance(b, str) and b:
                banners_text += " " + b
        banner_lower = banners_text.lower()

        if any(kw in banner_lower for kw in NETWORK_KEYWORDS):
            is_network_device = True

        detected_type = _detect_type_from_banner(banner_lower)
        if detected_type:
            device_type = detected_type
        if not vendor:
            v = _vendor_from_banner(banner_lower)
            if v:
                vendor = v

    if not is_network_device:
        return None

    # Fallback de tipo: porta de management mas banner inconclusivo.
    if device_type == "unknown" and (161 in port_nums or 23 in port_nums):
        device_type = "switch"

    # ── manageable_via ───────────────────────────────────────────────────
    manageable_via: list[str] = []
    if 80 in port_nums or 443 in port_nums:
        manageable_via.append("Web UI")
    if 22 in port_nums:
        manageable_via.append("SSH")
    if 23 in port_nums:
        manageable_via.append("Telnet")
    if 161 in port_nums:
        manageable_via.append("SNMP")

    return {
        "ip": ip,
        "mac": mac,
        "vendor": vendor or "Desconhecido",
        "device_type": device_type,
        "open_ports": open_ports,
        "manageable_via": manageable_via,
    }


# ─── WebSocket-driven scan orchestration ────────────────────────────────────

async def _ws_send_safe(ws, msg: dict) -> bool:
    """Envia JSON pelo WebSocket; False se o cliente já fechou (sinaliza parar)."""
    try:
        await ws.send_json(msg)
        return True
    except Exception:
        return False


async def scan_network(network: str, websocket) -> None:
    """
    Varre o range CIDR (ex.: '192.168.1.0/24') em paralelo e empurra
    eventos para o `websocket` à medida que cada host é resolvido.
    """
    try:
        net = ipaddress.ip_network(network, strict=False)
    except ValueError as e:
        await _ws_send_safe(websocket, {"type": "error", "message": f"Range inválido: {e}"})
        return

    hosts = [str(h) for h in net.hosts()] or [str(net.network_address)]
    total = len(hosts)

    if total > _MAX_HOSTS_PER_SCAN:
        await _ws_send_safe(websocket, {
            "type": "error",
            "message": (
                f"Range com {total} hosts — limite é {_MAX_HOSTS_PER_SCAN}. "
                f"Use /20 ou mais específico."
            ),
        })
        return

    if not await _ws_send_safe(websocket, {"type": "scan_started", "total": total}):
        return

    semaphore = asyncio.Semaphore(50)
    found_lock = asyncio.Lock()
    found_count = [0]
    aborted = asyncio.Event()

    async def process(ip: str) -> None:
        if aborted.is_set():
            return
        async with semaphore:
            if aborted.is_set():
                return

            # 1) Liveness — handshake TCP nas portas comuns em paralelo.
            probes = await asyncio.gather(
                *(_check_tcp_port(ip, p, timeout=0.5) for p in _LIVENESS_PORTS),
                return_exceptions=True,
            )
            alive = any(r is True for r in probes)
            if not alive:
                if not await _ws_send_safe(websocket, {
                    "type": "host_scanned", "ip": ip, "alive": False,
                }):
                    aborted.set()
                return

            # 2) MAC (via ARP) + scan completo de portas em paralelo.
            mac_task = asyncio.create_task(get_mac(ip))
            open_ports = await scan_ports(ip, timeout=0.5)
            mac = await mac_task

            # 3) Classifica.
            device = await classify_device(ip, mac, open_ports)

            if device:
                async with found_lock:
                    found_count[0] += 1
                if not await _ws_send_safe(websocket, {
                    "type": "host_found", **device,
                }):
                    aborted.set()
            else:
                if not await _ws_send_safe(websocket, {
                    "type": "host_scanned", "ip": ip, "alive": True, "filtered": True,
                }):
                    aborted.set()

    tasks = [asyncio.create_task(process(ip)) for ip in hosts]
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        raise

    if not aborted.is_set():
        await _ws_send_safe(websocket, {
            "type": "scan_complete", "found": found_count[0],
        })


async def get_local_range() -> dict:
    """
    Tenta descobrir o IP local e devolver um range /24 como sugestão.
    Estratégia: primeiro socket.gethostbyname(hostname); se vier 127.x,
    cai no truque do UDP-connect (não envia pacote real — só faz o SO
    escolher a interface de saída).
    """
    local_ip: str | None = None
    try:
        hostname = socket.gethostname()
        candidate = socket.gethostbyname(hostname)
        if not candidate.startswith("127."):
            local_ip = candidate
    except Exception:
        pass

    if not local_ip:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
        except Exception:
            local_ip = "127.0.0.1"
        finally:
            s.close()

    parts = local_ip.split(".")
    suggested_range = (
        f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
        if len(parts) == 4
        else "192.168.1.0/24"
    )
    return {
        "local_ip": local_ip,
        "suggested_range": suggested_range,
    }

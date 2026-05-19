import asyncio
import logging
import httpx
from sqlalchemy import select
from .database import AsyncSessionLocal
from .models import Device, DeviceType, DeviceStatus, EventLog

logger = logging.getLogger(__name__)

class MonitorManager:
    def __init__(self, websocket_broadcast_callback):
        self.broadcast = websocket_broadcast_callback
        self.running = False

    async def _check_web_device(self, device_id: int, client: httpx.AsyncClient):
        """Check a single web device using its own DB session to avoid conflicts."""
        async with AsyncSessionLocal() as session:
            device = await session.get(Device, device_id)
            if not device:
                return

            failures = 0
            max_attempts = 3
            timeout = httpx.Timeout(10.0)

            for attempt in range(1, max_attempts + 1):
                try:
                    response = await client.get(device.address, timeout=timeout, follow_redirects=True)
                    if 200 <= response.status_code < 400:
                        failures = 0
                        break
                    else:
                        failures += 1
                except Exception:
                    # Catch ALL exceptions: httpx.RequestError, ValueError (bad URL), etc.
                    failures += 1
                
                if failures < max_attempts:
                    await asyncio.sleep(5)
            
            # Determine status
            new_status = DeviceStatus.DOWN if failures >= max_attempts else DeviceStatus.UP
            
            # Always update failure count
            device.failure_count = failures

            if new_status != device.status:
                old_status = device.status
                device.status = new_status
                
                # Log event
                log = EventLog(
                    device_id=device.id,
                    old_status=old_status,
                    new_status=new_status,
                    latency=None
                )
                session.add(log)
                await session.commit()
                
                logger.info(f"[Monitor] {device.name}: {old_status.value} → {new_status.value}")

                # Real-time WebSocket Alert
                await self.broadcast({
                    "type": "status_change",
                    "priority": "high" if new_status == DeviceStatus.DOWN else "info",
                    "device_id": device.id,
                    "device_name": device.name,
                    "status": new_status.value,
                    "is_muted": device.is_muted
                })
            else:
                # Commit updated failure_count even if status didn't change
                await session.commit()

    async def monitor_web_loop(self):
        logger.info("[Monitor] Web loop started")
        while self.running:
            try:
                # Collect device IDs first, then check each with its own session
                async with AsyncSessionLocal() as session:
                    result = await session.execute(
                        select(Device.id).where(Device.device_type == DeviceType.WEB)
                    )
                    device_ids = [row[0] for row in result.all()]
                
                if device_ids:
                    async with httpx.AsyncClient() as client:
                        tasks = [self._check_web_device(did, client) for did in device_ids]
                        await asyncio.gather(*tasks, return_exceptions=True)

            except Exception as e:
                logger.error(f"[Monitor] Loop error: {e}")
                        
            await asyncio.sleep(30)

    async def _check_hardware_device(self, device_id: int):
        import platform
        from .services.snmp_service import get_snmp_cpu
        async with AsyncSessionLocal() as session:
            device = await session.get(Device, device_id)
            if not device:
                return

            failures = 0
            max_attempts = 3
            cpu_usage = None
            is_up = False

            if not device.comunidade_snmp or not device.oid_cpu:
                # Fallback para ping quando SNMP não está configurado
                param_count = '-n' if platform.system().lower() == 'windows' else '-c'
                param_timeout = '-w' if platform.system().lower() == 'windows' else '-W'
                timeout_val = '1000' if platform.system().lower() == 'windows' else '1'
                
                for attempt in range(1, max_attempts + 1):
                    try:
                        loop = asyncio.get_running_loop()
                        import subprocess
                        def run_ping():
                            return subprocess.run(
                                ['ping', param_count, '1', param_timeout, timeout_val, device.address],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL
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
                        await asyncio.sleep(5)
                
                new_status = DeviceStatus.DOWN if failures >= max_attempts else DeviceStatus.UP
            else:
                for attempt in range(1, max_attempts + 1):
                    try:
                        cpu_usage = await get_snmp_cpu(
                            ip=device.address,
                            community=device.comunidade_snmp,
                            oid_str=device.oid_cpu,
                            version=device.versao_snmp or 'v2c'
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
                        await asyncio.sleep(5)
                
                new_status = DeviceStatus.DOWN if failures >= max_attempts else DeviceStatus.UP

                if is_up and cpu_usage is not None:
                    if cpu_usage > 90:
                        if device.ultimo_uso_cpu is not None and device.ultimo_uso_cpu > 90:
                            new_status = DeviceStatus.CRITICAL_OVERLOAD
                    else:
                        new_status = DeviceStatus.UP
                    
                    device.ultimo_uso_cpu = cpu_usage

            device.failure_count = failures

            if new_status != device.status:
                old_status = device.status
                device.status = new_status
                
                log = EventLog(
                    device_id=device.id,
                    old_status=old_status,
                    new_status=new_status,
                    latency=None
                )
                session.add(log)
                await session.commit()
                
                logger.info(f"[Monitor] {device.name}: {old_status.value} → {new_status.value}")

                await self.broadcast({
                    "type": "status_change",
                    "priority": "critical" if new_status == DeviceStatus.CRITICAL_OVERLOAD else ("high" if new_status == DeviceStatus.DOWN else "info"),
                    "device_id": device.id,
                    "device_name": device.name,
                    "status": new_status.value,
                    "is_muted": device.is_muted
                })
            else:
                await session.commit()

    async def monitor_hardware_loop(self):
        logger.info("[Monitor] Hardware loop started")
        while self.running:
            try:
                async with AsyncSessionLocal() as session:
                    result = await session.execute(
                        select(Device.id).where(Device.device_type == DeviceType.HARDWARE)
                    )
                    device_ids = [row[0] for row in result.all()]
                
                if device_ids:
                    tasks = [self._check_hardware_device(did) for did in device_ids]
                    await asyncio.gather(*tasks, return_exceptions=True)

            except Exception as e:
                logger.error(f"[Monitor] Hardware loop error: {e}")
            await asyncio.sleep(60)

    async def monitor_database_loop(self):
        logger.info("[Monitor] Database loop started")
        while self.running:
            try:
                # TODO: Implement DB query checks directly here
                pass
            except Exception as e:
                logger.error(f"[Monitor] Database loop error: {e}")
            await asyncio.sleep(60)

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

    async def monitor_loop(self):
        self.running = True
        logger.info("[Monitor] Loop started")
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
            
    def start(self):
        asyncio.create_task(self.monitor_loop())
    
    def stop(self):
        self.running = False

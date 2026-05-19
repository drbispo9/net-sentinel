import asyncio
import platform

async def test_ping():
    ip = "127.0.0.1"
    param_count = '-n' if platform.system().lower() == 'windows' else '-c'
    param_timeout = '-w' if platform.system().lower() == 'windows' else '-W'
    timeout_val = '1000' if platform.system().lower() == 'windows' else '1'
    
    proc = await asyncio.create_subprocess_exec(
        'ping', param_count, '1', param_timeout, timeout_val, ip,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    print("Localhost (127.0.0.1) ping exit code:", proc.returncode)

    ip = "192.0.2.1" # Non-routable dummy IP
    proc = await asyncio.create_subprocess_exec(
        'ping', param_count, '1', param_timeout, timeout_val, ip,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    print("Dummy IP (192.0.2.1) ping exit code:", proc.returncode)

if __name__ == "__main__":
    asyncio.run(test_ping())

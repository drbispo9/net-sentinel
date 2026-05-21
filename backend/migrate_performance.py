"""
migrate_performance.py
======================
One-shot migration: creates the performance_logs table if it doesn't exist.
Run with:  python -m backend.migrate_performance
           (from the project root, with venv activated)
"""
import asyncio
import sys
import os

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from backend.database import engine, Base
from backend.models import PerformanceLog  # ensure model is registered  # noqa: F401


async def migrate():
    print("[migrate_performance] Connecting to database...")
    async with engine.begin() as conn:
        # Create performance_logs if missing -- safe to run multiple times
        await conn.run_sync(Base.metadata.create_all)
        print("[migrate_performance] [OK] Table 'performance_logs' is ready.")

        # Verify
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='performance_logs'"
        ))
        row = result.fetchone()
        if row:
            print(f"[migrate_performance] [OK] Confirmed: table '{row[0]}' exists in SQLite.")
        else:
            print("[migrate_performance] [WARN] Table not found -- check your DATABASE_URL config.")



if __name__ == "__main__":
    asyncio.run(migrate())

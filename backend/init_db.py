import asyncio
from backend.database import engine, Base
from backend.models import Device, EventLog # Import to register the models

async def init_models():
    async with engine.begin() as conn:
        # Create tables
        await conn.run_sync(Base.metadata.create_all)
    print("Database tables created.")

if __name__ == "__main__":
    asyncio.run(init_models())

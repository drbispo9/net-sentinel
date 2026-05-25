import os
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

# Ensure .env is loaded even when this module is imported before main.py
load_dotenv(override=False)


# The default falls back to sqlite for ease of development if no DB is provided,
# but the Instructions request PostgreSQL and SQLAlchemy. We'll use sqlite here as default just to not crash,
# but in production `.env` should have postgresql+asyncpg://
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./netsentinel.db")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

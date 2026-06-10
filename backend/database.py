import os
from dotenv import load_dotenv
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

# Ensure .env is loaded even when this module is imported before main.py
load_dotenv(override=False)


# The default falls back to sqlite for ease of development if no DB is provided,
# but the Instructions request PostgreSQL and SQLAlchemy. We'll use sqlite here as default just to not crash,
# but in production `.env` should have postgresql+asyncpg://
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./netsentinel.db")

engine = create_async_engine(DATABASE_URL, echo=False)


# ── Pragmas do SQLite (aplicados a cada nova conexão) ─────────────────────────
# Os loops de monitoramento disparam commits CONCORRENTES (asyncio.gather sobre
# todos os devices/monitores). No SQLite padrão isso gera "database is locked".
#   - journal_mode=WAL   → leitores não bloqueiam escritor e vice-versa.
#   - busy_timeout=5000  → espera até 5s por um lock em vez de falhar na hora.
#   - foreign_keys=ON    → faz o ON DELETE CASCADE realmente funcionar (sem isso
#                          o SQLite ignora as FKs e deixa EventLog órfão ao
#                          deletar um monitor).
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

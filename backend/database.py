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

IS_SQLITE = DATABASE_URL.startswith("sqlite")
IS_POSTGRES = DATABASE_URL.startswith("postgresql")

# Pool de conexões: o SQLite (single-file) não se beneficia de um pool grande,
# mas o PostgreSQL sim — os loops de monitoramento abrem várias sessões em
# paralelo (asyncio.gather sobre devices/monitores).
#   - pool_pre_ping  → descarta conexões mortas (timeout do servidor, restart do
#                      container) antes de usá-las, evitando erros intermitentes.
#   - pool_recycle   → recicla conexões a cada 30 min (abaixo do idle_timeout
#                      padrão do Postgres) para não acumular sockets zumbis.
_engine_kwargs = {"echo": False}
if IS_POSTGRES:
    _engine_kwargs.update(
        pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "20")),
        pool_pre_ping=True,
        pool_recycle=1800,
    )

engine = create_async_engine(DATABASE_URL, **_engine_kwargs)


# ── Pragmas do SQLite (aplicados a cada nova conexão) ─────────────────────────
# Os loops de monitoramento disparam commits CONCORRENTES (asyncio.gather sobre
# todos os devices/monitores). No SQLite padrão isso gera "database is locked".
#   - journal_mode=WAL   → leitores não bloqueiam escritor e vice-versa.
#   - busy_timeout=5000  → espera até 5s por um lock em vez de falhar na hora.
#   - foreign_keys=ON    → faz o ON DELETE CASCADE realmente funcionar (sem isso
#                          o SQLite ignora as FKs e deixa EventLog órfão ao
#                          deletar um monitor).
if IS_SQLITE:
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

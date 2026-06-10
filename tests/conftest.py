"""
Fixtures de teste do NetSentinel.

Cada teste usa um banco SQLite em memória isolado (StaticPool mantém a mesma
conexão, então as tabelas persistem durante o teste). A dependência `get_db`
é sobrescrita para usar esse banco, e o `ASGITransport` do httpx exercita o
app sem subir os loops de monitoramento (o lifespan não roda no transport).
"""

import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from backend.database import Base, get_db
from backend import main as main_module


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Espelha o pragma de produção: sem foreign_keys=ON os testes não pegariam
    # erros de constraint (ex.: deletar device com EventLog associado).
    @event.listens_for(eng.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def client(session_factory):
    async def override_get_db():
        async with session_factory() as session:
            yield session

    app = main_module.app
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()

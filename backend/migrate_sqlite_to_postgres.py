"""
backend/migrate_sqlite_to_postgres.py
─────────────────────────────────────
Copia TODOS os dados de um banco SQLite existente para o PostgreSQL de destino,
preservando os IDs e, ao final, reajustando as sequences (SERIAL) do Postgres
para que novos INSERTs não colidam com os IDs copiados.

Uso (a partir da raiz do projeto, com o venv ativo e o Postgres já no ar):

    python -m backend.migrate_sqlite_to_postgres \
        --sqlite sqlite+aiosqlite:///./netsentinel.db \
        --postgres postgresql+asyncpg://netsentinel:netsentinel@localhost:5432/netsentinel

Se os argumentos forem omitidos:
  --sqlite   assume ./netsentinel.db
  --postgres assume a variável de ambiente DATABASE_URL (deve apontar p/ Postgres)

É idempotente no sentido de que cria o schema no destino (create_all) antes de
copiar; porém NÃO limpa o destino — rode contra um banco Postgres VAZIO para
evitar conflito de chave primária.
"""

import argparse
import asyncio
import os
import sys

import asyncpg
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# Acima deste nº de linhas, usa COPY (protocolo binário do asyncpg) em vez de
# INSERT/executemany. O executemany do SQLAlchemy+asyncpg faz o cliente virar
# gargalo e leva minutos para >100k linhas; o COPY resolve em segundos.
COPY_THRESHOLD = 500

from backend.database import Base
# Importa todos os models para registrá-los no metadata.
from backend.models import (
    Device, EventLog, PerformanceLog, DatabaseMonitor, AppSetting, MaintenanceWindow,
)

# Ordem que respeita as FKs (pais antes dos filhos).
ORDERED_MODELS = [
    Device,
    DatabaseMonitor,
    AppSetting,
    EventLog,
    PerformanceLog,
    MaintenanceWindow,
]

# FKs a validar antes de inserir no Postgres. O SQLite não aplicava FKs, então
# sobraram logs órfãos (apontando para devices/monitores já deletados) que o
# Postgres recusa. Cada entrada mapeia: model → [(coluna_fk, model_pai)].
# Linhas com FK não-nulo apontando para um pai inexistente são DESCARTADAS.
FK_CONSTRAINTS = {
    EventLog: [("device_id", Device), ("db_monitor_id", DatabaseMonitor)],
    PerformanceLog: [("device_id", Device)],
    MaintenanceWindow: [("device_id", Device), ("db_monitor_id", DatabaseMonitor)],
}


def _row_to_dict(obj) -> dict:
    """Extrai as colunas mapeadas de uma instância ORM como dict puro."""
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _asyncpg_dsn(postgres_url: str) -> str:
    """Converte o URL SQLAlchemy (postgresql+asyncpg://) no DSN puro do asyncpg."""
    return postgres_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _copy_bulk(postgres_url: str, table: str, columns: list, payload: list) -> None:
    """
    Insere em massa via COPY (protocolo binário do asyncpg) — ordens de
    magnitude mais rápido que executemany para tabelas grandes.
    Abre uma conexão asyncpg dedicada (em autocommit) para não interferir nas
    transações do SQLAlchemy.
    """
    records = [tuple(r.get(c) for c in columns) for r in payload]
    conn = await asyncpg.connect(_asyncpg_dsn(postgres_url))
    try:
        await conn.copy_records_to_table(table, records=records, columns=columns)
    finally:
        await conn.close()


async def _reset_sequences(session: AsyncSession, model) -> None:
    """
    Reajusta a sequence da PK inteira (id) para MAX(id)+1 no Postgres.
    Usa pg_get_serial_sequence para descobrir o nome real da sequence.
    Pula tabelas sem coluna 'id' autoincrementável (ex.: app_settings).
    """
    table = model.__tablename__
    pk_cols = [c.name for c in model.__table__.primary_key.columns]
    if pk_cols != ["id"]:
        return  # PK não-serial (ex.: app_settings.key) → nada a reajustar.

    await session.execute(text(
        f"""
        SELECT setval(
            pg_get_serial_sequence('{table}', 'id'),
            COALESCE((SELECT MAX(id) FROM {table}), 1),
            (SELECT MAX(id) IS NOT NULL FROM {table})
        )
        """
    ))


async def migrate(sqlite_url: str, postgres_url: str) -> None:
    if not postgres_url.startswith("postgresql"):
        print(f"[ERRO] --postgres não parece um URL Postgres: {postgres_url}", file=sys.stderr)
        sys.exit(1)

    src_engine = create_async_engine(sqlite_url, echo=False)
    dst_engine = create_async_engine(postgres_url, echo=False)

    Src = async_sessionmaker(src_engine, class_=AsyncSession, expire_on_commit=False)
    Dst = async_sessionmaker(dst_engine, class_=AsyncSession, expire_on_commit=False)

    # 1) Garante o schema no destino.
    async with dst_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[migrate] Schema garantido no PostgreSQL.")

    # 1b) Limpa as tabelas de destino (idempotência: permite re-rodar a migração
    # sem conflito de PK). RESTART IDENTITY zera as sequences; CASCADE respeita FKs.
    tables = ", ".join(m.__tablename__ for m in ORDERED_MODELS)
    async with dst_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    print(f"[migrate] Tabelas de destino limpas: {tables}")

    total = 0
    skipped = 0
    # IDs válidos já migrados, por model-pai — usados para descartar órfãos.
    valid_ids: dict = {}

    # 2) Copia tabela por tabela, na ordem das FKs.
    for model in ORDERED_MODELS:
        async with Src() as src:
            rows = (await src.execute(select(model))).scalars().all()
        if not rows:
            print(f"[migrate] {model.__tablename__}: 0 registros (pulado).")
            # Registra conjunto vazio de IDs caso seja pai de alguém.
            pk_cols = [c.name for c in model.__table__.primary_key.columns]
            if pk_cols == ["id"]:
                valid_ids[model] = set()
            continue

        payload = [_row_to_dict(r) for r in rows]

        # Descarta linhas órfãs (FK não-nulo apontando para pai inexistente).
        constraints = FK_CONSTRAINTS.get(model, [])
        if constraints:
            kept = []
            for r in payload:
                ok = True
                for fk_col, parent_model in constraints:
                    fk_val = r.get(fk_col)
                    if fk_val is not None and fk_val not in valid_ids.get(parent_model, set()):
                        ok = False
                        break
                if ok:
                    kept.append(r)
            dropped = len(payload) - len(kept)
            if dropped:
                skipped += dropped
                print(f"[migrate] {model.__tablename__}: {dropped} registros órfãos DESCARTADOS.")
            payload = kept

        if payload:
            if len(payload) > COPY_THRESHOLD:
                # Tabela grande → COPY (rápido).
                columns = [c.name for c in model.__table__.columns]
                await _copy_bulk(postgres_url, model.__tablename__, columns, payload)
            else:
                async with Dst() as dst:
                    await dst.execute(model.__table__.insert(), payload)
                    await dst.commit()
        total += len(payload)
        print(f"[migrate] {model.__tablename__}: {len(payload)} registros copiados.")

        # Se este model é pai (tem PK 'id'), guarda seus IDs válidos.
        pk_cols = [c.name for c in model.__table__.primary_key.columns]
        if pk_cols == ["id"]:
            valid_ids[model] = {r["id"] for r in payload}

    # 3) Reajusta as sequences para não colidir com os IDs copiados.
    async with Dst() as dst:
        for model in ORDERED_MODELS:
            await _reset_sequences(dst, model)
        await dst.commit()
    print("[migrate] Sequences reajustadas.")

    await src_engine.dispose()
    await dst_engine.dispose()
    print(f"[migrate] Concluído. Total: {total} registros migrados, {skipped} órfãos descartados.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migra dados SQLite → PostgreSQL (NetSentinel).")
    parser.add_argument(
        "--sqlite",
        default="sqlite+aiosqlite:///./netsentinel.db",
        help="URL do SQLite de origem (default: ./netsentinel.db)",
    )
    parser.add_argument(
        "--postgres",
        default=os.getenv("DATABASE_URL", ""),
        help="URL do PostgreSQL de destino (default: env DATABASE_URL)",
    )
    args = parser.parse_args()

    if not args.postgres:
        print("[ERRO] Informe --postgres ou defina DATABASE_URL no ambiente.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(migrate(args.sqlite, args.postgres))


if __name__ == "__main__":
    main()

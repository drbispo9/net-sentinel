"""
backend/migrations.py
─────────────────────
Migração idempotente de schema para SQLite, executada na inicialização.

`Base.metadata.create_all` cria tabelas novas, mas NÃO adiciona colunas novas a
tabelas que já existem em bancos antigos. Esta função preenche essa lacuna:
para cada tabela existente, compara as colunas reais (via PRAGMA table_info)
com as esperadas e adiciona apenas as que faltam.

Consolida o que antes estava espalhado pelos scripts manuais
`migrate_db.py`, `migrate_dns.py`, `migrate_slug.py`, `migrate_keyword.py`,
`migrate_db_monitors.py` e `migrate_performance.py` — agora roda sozinho,
toda vez que o servidor sobe, sem risco de duplicar colunas.

Só atua em SQLite. Em PostgreSQL recomenda-se uma ferramenta dedicada
(ex.: Alembic).
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

# Colunas esperadas por tabela, com o tipo SQLite usado no ALTER TABLE.
# As CHAVES (nomes de tabela/coluna) são fixas e confiáveis — nunca vêm do
# usuário — então é seguro interpolá-las na DDL.
_EXPECTED_COLUMNS = {
    "devices": {
        "response_time_ms":    "INTEGER",
        "dns_ms":              "FLOAT",
        "slug_identificador":  "VARCHAR(50)",
        "validar_texto":       "BOOLEAN DEFAULT 0",
        "texto_obrigatorio":   "TEXT",
        "comunidade_snmp":     "TEXT",
        "versao_snmp":         "TEXT",
        "oid_cpu":             "TEXT",
        "ultimo_uso_cpu":      "FLOAT",
        "status_portas":       "TEXT",
    },
    "event_logs": {
        "db_monitor_id":       "INTEGER",
        "lock_count":          "INTEGER",
    },
}

# Índices esperados: nome → (tabela, colunas). Criados com IF NOT EXISTS, então
# é seguro rodar sempre. create_all NÃO adiciona índices a tabelas já existentes.
_EXPECTED_INDEXES = {
    "ix_event_logs_db_monitor_timestamp": ("event_logs", "(db_monitor_id, timestamp)"),
}


def run_migrations(connection) -> None:
    """
    Executada via `await conn.run_sync(run_migrations)` — recebe uma conexão
    SQLAlchemy *síncrona*. Adiciona colunas faltantes de forma idempotente.
    """
    if connection.dialect.name != "sqlite":
        # Em outros bancos, deixa a cargo de migrações dedicadas.
        return

    for table, columns in _EXPECTED_COLUMNS.items():
        # A tabela existe? (Se não, create_all já cuidou com o schema completo.)
        exists = connection.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": table},
        ).first()
        if not exists:
            continue

        existing = {
            row[1]  # row = (cid, name, type, notnull, dflt_value, pk)
            for row in connection.execute(text(f"PRAGMA table_info({table})")).fetchall()
        }

        for col, ddl in columns.items():
            if col not in existing:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
                logger.info("[migrations] Coluna adicionada: %s.%s (%s)", table, col, ddl)

    # ── Índices (idempotentes via IF NOT EXISTS) ─────────────────────────────
    for index_name, (table, columns_sql) in _EXPECTED_INDEXES.items():
        exists = connection.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": table},
        ).first()
        if not exists:
            continue
        connection.execute(
            text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} {columns_sql}")
        )

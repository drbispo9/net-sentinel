"""
Migration: creates the database_monitors table.
Safe to run multiple times (checks for existence first).
"""
import sys, sqlite3, os
sys.stdout.reconfigure(encoding='utf-8')

db_path = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'netsentinel.db'))
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Check if table already exists
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='database_monitors'")
if cur.fetchone():
    print("Table 'database_monitors' already exists — skipping creation.")
else:
    cur.execute("""
        CREATE TABLE database_monitors (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            nome                    TEXT    NOT NULL,
            endpoint_url            TEXT    NOT NULL,
            status                  TEXT    NOT NULL DEFAULT 'UP',
            is_muted                INTEGER NOT NULL DEFAULT 0,
            ultimo_total_locks      INTEGER          DEFAULT 0,
            consecutive_lock_count  INTEGER NOT NULL DEFAULT 0,
            created_at              TEXT             DEFAULT (datetime('now')),
            updated_at              TEXT             DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    print("OK: table 'database_monitors' created successfully.")

conn.close()

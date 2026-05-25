"""
Migration: Add keyword matching fields to the devices table.

Adds two new columns to support content validation (Keyword Matching):
  - validar_texto      BOOLEAN  DEFAULT 0
  - texto_obrigatorio  TEXT     DEFAULT NULL

Safe to run multiple times — already-existing columns are silently ignored.
"""
import sqlite3
import os


def migrate():
    db_path = os.path.join(os.path.dirname(__file__), "..", "netsentinel.db")
    db_path = os.path.normpath(db_path)

    if not os.path.exists(db_path):
        print(f"[migrate_keyword] Database not found at: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    migrations = [
        ("validar_texto",      "ALTER TABLE devices ADD COLUMN validar_texto BOOLEAN DEFAULT 0;"),
        ("texto_obrigatorio",  "ALTER TABLE devices ADD COLUMN texto_obrigatorio TEXT DEFAULT NULL;"),
    ]

    for col_name, sql in migrations:
        try:
            cursor.execute(sql)
            conn.commit()
            print(f"[migrate_keyword] ✓ Column '{col_name}' added successfully.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print(f"[migrate_keyword] ⚠ Column '{col_name}' already exists — skipped.")
            else:
                print(f"[migrate_keyword] ✗ Error adding '{col_name}': {e}")

    conn.close()
    print("[migrate_keyword] Migration complete.")


if __name__ == "__main__":
    migrate()

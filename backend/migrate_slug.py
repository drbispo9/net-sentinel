import sqlite3
import os

def migrate():
    db_path = "netsentinel.db"
    if not os.path.exists(db_path):
        print(f"[migrate_slug] Database {db_path} not found.")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE devices ADD COLUMN slug_identificador TEXT DEFAULT NULL;")
        conn.commit()
        print("[migrate_slug] Column 'slug_identificador' added successfully to 'devices' table.")
    except sqlite3.OperationalError as e:
        print(f"[migrate_slug] Column may already exist: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()

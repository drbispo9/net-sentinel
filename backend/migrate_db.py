import sqlite3
import os

def migrate():
    db_path = "netsentinel.db"
    if not os.path.exists(db_path):
        print(f"Database {db_path} not found.")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE devices ADD COLUMN response_time_ms INTEGER DEFAULT NULL;")
        conn.commit()
        print("Column response_time_ms added to devices table successfully.")
    except sqlite3.OperationalError as e:
        print(f"Column may already exist: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()

#!/usr/bin/env python3
"""
Migration script to add installation_date column to customers table
"""
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "autoispbilling.db"

def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Check if column already exists
        cursor.execute("PRAGMA table_info(customers)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'installation_date' not in columns:
            print("Adding installation_date column to customers table...")
            cursor.execute("ALTER TABLE customers ADD COLUMN installation_date TEXT")
            conn.commit()
            print("✓ Migration completed successfully!")
        else:
            print("✓ Column installation_date already exists, skipping migration.")
    
    except Exception as e:
        print(f"✗ Migration failed: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()

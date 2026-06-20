#!/usr/bin/env python3
"""
Migration script to add received_amount column to customers table
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
        
        if 'received_amount' not in columns:
            print("Adding received_amount column to customers table...")
            cursor.execute("ALTER TABLE customers ADD COLUMN received_amount REAL")
            conn.commit()
            print("✓ Migration completed successfully!")
        else:
            print("✓ Column received_amount already exists, skipping migration.")
    
    except Exception as e:
        print(f"✗ Migration failed: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()

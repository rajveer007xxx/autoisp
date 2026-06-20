#!/usr/bin/env python3
"""Fix missing created_at and updated_at columns in database"""
import sys
import sqlite3
from datetime import datetime

sys.path.insert(0, '/home/ubuntu/autoispbilling-payfast-repo')

DB_PATH = '/var/lib/autoispbilling/autoispbilling.db'

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

try:
    # Fix admins table
    print("Fixing admins table...")
    cursor.execute("PRAGMA table_info(admins)")
    admin_columns = [row[1] for row in cursor.fetchall()]
    
    if 'created_at' not in admin_columns:
        cursor.execute("ALTER TABLE admins ADD COLUMN created_at TIMESTAMP")
        cursor.execute("UPDATE admins SET created_at = datetime('now') WHERE created_at IS NULL")
        print("✓ Added created_at to admins")
    
    if 'updated_at' not in admin_columns:
        cursor.execute("ALTER TABLE admins ADD COLUMN updated_at TIMESTAMP")
        cursor.execute("UPDATE admins SET updated_at = datetime('now') WHERE updated_at IS NULL")
        print("✓ Added updated_at to admins")
    
    # Fix companies table
    print("\nFixing companies table...")
    cursor.execute("PRAGMA table_info(companies)")
    company_columns = [row[1] for row in cursor.fetchall()]
    
    if 'created_at' not in company_columns:
        cursor.execute("ALTER TABLE companies ADD COLUMN created_at TIMESTAMP")
        cursor.execute("UPDATE companies SET created_at = datetime('now') WHERE created_at IS NULL")
        print("✓ Added created_at to companies")
    
    if 'updated_at' not in company_columns:
        cursor.execute("ALTER TABLE companies ADD COLUMN updated_at TIMESTAMP")
        cursor.execute("UPDATE companies SET updated_at = datetime('now') WHERE updated_at IS NULL")
        print("✓ Added updated_at to companies")
    
    # Fix employees table
    print("\nFixing employees table...")
    cursor.execute("PRAGMA table_info(employees)")
    employee_columns = [row[1] for row in cursor.fetchall()]
    
    if 'created_at' not in employee_columns:
        cursor.execute("ALTER TABLE employees ADD COLUMN created_at TIMESTAMP")
        cursor.execute("UPDATE employees SET created_at = datetime('now') WHERE created_at IS NULL")
        print("✓ Added created_at to employees")
    
    if 'updated_at' not in employee_columns:
        cursor.execute("ALTER TABLE employees ADD COLUMN updated_at TIMESTAMP")
        cursor.execute("UPDATE employees SET updated_at = datetime('now') WHERE updated_at IS NULL")
        print("✓ Added updated_at to employees")
    
    conn.commit()
    print("\n✓ All missing columns added successfully")
    
except Exception as e:
    print(f"✗ Error: {e}")
    conn.rollback()
finally:
    conn.close()

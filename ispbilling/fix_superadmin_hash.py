#!/usr/bin/env python3
"""Fix superadmin password hash in database"""
import sys
sys.path.insert(0, '/home/ubuntu/autoispbilling-payfast-repo')

from database import SessionLocal, SuperAdmin
from auth import get_password_hash

db = SessionLocal()
try:
    # Get superadmin
    superadmin = db.query(SuperAdmin).filter(SuperAdmin.superadmin_id == "rajveersuper007@").first()
    
    if superadmin:
        # Generate correct password hash
        correct_hash = get_password_hash("Pa$$word@123")
        print(f"Generated hash length: {len(correct_hash)}")
        print(f"Generated hash: {correct_hash}")
        
        # Update password hash
        superadmin.password_hash = correct_hash
        db.commit()
        
        # Verify
        db.refresh(superadmin)
        print(f"Updated hash length: {len(superadmin.password_hash)}")
        print(f"✓ Superadmin password hash updated successfully")
    else:
        print("✗ Superadmin not found")
        
finally:
    db.close()

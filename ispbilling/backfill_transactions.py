#!/usr/bin/env python3
"""
Backfill script to fix incorrect Transaction records created by the buggy payment endpoint.
This script updates Transaction rows where:
- company_id != 'SUPERADMIN' (should be 'SUPERADMIN' for admin subscription transactions)
- customer_id is None (should be the admin's company_id)
- note contains 'Admin payment' (identifies these as admin payment transactions)
"""

import sys
sys.path.insert(0, '/home/ubuntu/autoispbilling-payfast-repo')

from database import SessionLocal, Transaction
from sqlalchemy import and_

def backfill_transactions():
    db = SessionLocal()
    try:
        # Find all incorrectly created admin payment transactions
        incorrect_transactions = db.query(Transaction).filter(
            and_(
                Transaction.company_id != 'SUPERADMIN',
                Transaction.customer_id == None,
                Transaction.note.like('%Admin payment%')
            )
        ).all()
        
        print(f"Found {len(incorrect_transactions)} incorrect transaction records")
        
        if len(incorrect_transactions) == 0:
            print("No transactions to backfill")
            return
        
        # Fix each transaction
        for trans in incorrect_transactions:
            old_company_id = trans.company_id
            print(f"Fixing transaction ID {trans.id}: company_id={old_company_id} -> SUPERADMIN, customer_id=None -> {old_company_id}")
            
            # Set correct values
            trans.customer_id = old_company_id  # The admin's company_id
            trans.company_id = 'SUPERADMIN'     # All admin subscription transactions use SUPERADMIN
            trans.transaction_type = 'payment'  # Ensure transaction_type is set
        
        db.commit()
        print(f"Successfully backfilled {len(incorrect_transactions)} transaction records")
        
    except Exception as e:
        db.rollback()
        print(f"Error backfilling transactions: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    backfill_transactions()

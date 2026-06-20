#!/usr/bin/env python3
"""
Auto-Renewal Script for Admin Subscriptions
Runs daily at 2:00 AM IST to check for expired admins and auto-renew them
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from database import SessionLocal, Company
from admin_renewal_helper import renew_admin_subscription
import traceback


def get_expired_admins(db: Session):
    """Get all expired admins that need auto-renewal"""
    today = datetime.now().date()
    
    expired_admins = db.query(Company).filter(
        Company.admin_type != "Trial",
        Company.end_date <= today,
        Company.auto_renew_enabled == 1
    ).all()
    
    return expired_admins


def main():
    """Main function to process auto-renewals"""
    print(f"\n{'='*80}")
    print(f"Auto-Renewal Job Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}")
    print(f"{'='*80}\n")
    
    db = SessionLocal()
    
    try:
        expired_admins = get_expired_admins(db)
        
        if not expired_admins:
            print("No expired admins found for auto-renewal.")
            return
        
        print(f"Found {len(expired_admins)} expired admin(s) for auto-renewal:\n")
        
        success_count = 0
        failure_count = 0
        skipped_count = 0
        
        for company in expired_admins:
            print(f"\nProcessing: {company.company_name} (ID: {company.company_id})")
            print(f"  Package: {company.package}")
            print(f"  End Date: {company.end_date}")
            print(f"  Current Balance: ₹{company.balance_amount or 0:.2f}")
            print(f"  Renewal Period: {company.period_months or 1} month(s)")
            
            try:
                months = company.period_months or 1
                gst_invoice_needed = bool(company.gst_invoice_needed) if hasattr(company, 'gst_invoice_needed') and company.gst_invoice_needed is not None else True
                
                result = renew_admin_subscription(db, company.company_id, months, method="auto", gst_invoice_needed=gst_invoice_needed)
                
                if result["success"]:
                    print(f"  ✓ SUCCESS: Renewed for {months} month(s)")
                    print(f"    Invoice: {result['invoice_no']}")
                    print(f"    Amount: ₹{result['amount']:.2f}")
                    print(f"    New End Date: {result['new_end_date']}")
                    print(f"    New Balance: ₹{result['new_balance']:.2f}")
                    print(f"    Email Sent: {'Yes' if result.get('email_sent') else 'No'}")
                    success_count += 1
                elif result.get("duplicate"):
                    print(f"  ⊘ SKIPPED: {result['error']}")
                    skipped_count += 1
                else:
                    print(f"  ✗ FAILED: {result['error']}")
                    failure_count += 1
                    
            except Exception as e:
                print(f"  ✗ ERROR: {str(e)}")
                traceback.print_exc()
                failure_count += 1
        
        print(f"\n{'='*80}")
        print(f"Auto-Renewal Job Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}")
        print(f"{'='*80}")
        print(f"\nSummary:")
        print(f"  Total Processed: {len(expired_admins)}")
        print(f"  Successful: {success_count}")
        print(f"  Failed: {failure_count}")
        print(f"  Skipped: {skipped_count}")
        print(f"\n")
        
    except Exception as e:
        print(f"\n✗ FATAL ERROR: {str(e)}")
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()

# __S38X_REPLY_EMAIL__
from fastapi import FastAPI, Request, Depends, Form, BackgroundTasks, HTTPException, UploadFile, File, Query
from db_compat import get_raw_conn as _compat_conn  # __s56Z_compat__
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func, text, select, or_, and_
from sqlalchemy.exc import IntegrityError
import uvicorn
import random
import string
import os
from datetime import datetime, timedelta

from database import init_db, get_db, SessionLocal, Customer
from auth import authenticate_admin, authenticate_employee, authenticate_customer, authenticate_superadmin

INDIAN_STATES = {
    'andhra pradesh': ('Andhra Pradesh', '37'), 'arunachal pradesh': ('Arunachal Pradesh', '12'),
    'assam': ('Assam', '18'), 'bihar': ('Bihar', '10'), 'chhattisgarh': ('Chhattisgarh', '22'),
    'goa': ('Goa', '30'), 'gujarat': ('Gujarat', '24'), 'haryana': ('Haryana', '06'),
    'himachal pradesh': ('Himachal Pradesh', '02'), 'jharkhand': ('Jharkhand', '20'),
    'karnataka': ('Karnataka', '29'), 'kerala': ('Kerala', '32'), 'madhya pradesh': ('Madhya Pradesh', '23'),
    'maharashtra': ('Maharashtra', '27'), 'manipur': ('Manipur', '14'), 'meghalaya': ('Meghalaya', '17'),
    'mizoram': ('Mizoram', '15'), 'nagaland': ('Nagaland', '13'), 'odisha': ('Odisha', '21'),
    'punjab': ('Punjab', '03'), 'rajasthan': ('Rajasthan', '08'), 'sikkim': ('Sikkim', '11'),
    'tamil nadu': ('Tamil Nadu', '33'), 'telangana': ('Telangana', '36'), 'tripura': ('Tripura', '16'),
    'uttar pradesh': ('Uttar Pradesh', '09'), 'uttarakhand': ('Uttarakhand', '05'),
    'west bengal': ('West Bengal', '19'), 'andaman and nicobar islands': ('Andaman and Nicobar Islands', '35'),
    'chandigarh': ('Chandigarh', '04'), 'dadra and nagar haveli and daman and diu': ('Dadra and Nagar Haveli and Daman and Diu', '26'),
    'delhi': ('Delhi', '07'), 'jammu and kashmir': ('Jammu and Kashmir', '01'), 'ladakh': ('Ladakh', '38'),
    'lakshadweep': ('Lakshadweep', '31'), 'puducherry': ('Puducherry', '34'),
    'ap': ('Andhra Pradesh', '37'), 'ar': ('Arunachal Pradesh', '12'), 'as': ('Assam', '18'),
    'br': ('Bihar', '10'), 'cg': ('Chhattisgarh', '22'), 'ga': ('Goa', '30'), 'gj': ('Gujarat', '24'),
    'hr': ('Haryana', '06'), 'hp': ('Himachal Pradesh', '02'), 'jh': ('Jharkhand', '20'),
    'ka': ('Karnataka', '29'), 'kl': ('Kerala', '32'), 'mp': ('Madhya Pradesh', '23'),
    'mh': ('Maharashtra', '27'), 'mn': ('Manipur', '14'), 'ml': ('Meghalaya', '17'),
    'mz': ('Mizoram', '15'), 'nl': ('Nagaland', '13'), 'or': ('Odisha', '21'), 'pb': ('Punjab', '03'),
    'rj': ('Rajasthan', '08'), 'sk': ('Sikkim', '11'), 'tn': ('Tamil Nadu', '33'),
    'tg': ('Telangana', '36'), 'tr': ('Tripura', '16'), 'up': ('Uttar Pradesh', '09'),
    'uk': ('Uttarakhand', '05'), 'wb': ('West Bengal', '19'), 'an': ('Andaman and Nicobar Islands', '35'),
    'ch': ('Chandigarh', '04'), 'dnh': ('Dadra and Nagar Haveli and Daman and Diu', '26'),
    'dd': ('Dadra and Nagar Haveli and Daman and Diu', '26'), 'dl': ('Delhi', '07'),
    'jk': ('Jammu and Kashmir', '01'), 'la': ('Ladakh', '38'), 'ld': ('Lakshadweep', '31'),
    'py': ('Puducherry', '34'),
}

def get_state_info(state_value):
    """Get full state name and GST state code from state value."""
    if not state_value:
        return ('', None)
    normalized = state_value.strip().lower().replace('.', '').replace('  ', ' ')
    if normalized in INDIAN_STATES:
        return INDIAN_STATES[normalized]
    return (state_value.strip(), None)

app = FastAPI()

app.add_middleware(SessionMiddleware, secret_key="your-secret-key-change-in-production-12345678")

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")
templates.env.auto_reload = True

@app.on_event("startup")
def startup_event():
    import sqlite3
    from database import DB_PATH
    from pathlib import Path
    
    db_dir = Path(DB_PATH).parent
    db_dir.mkdir(parents=True, exist_ok=True)
    
    init_db()
    
    conn = _compat_conn()
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA table_info(customers)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if 'customer_type' not in columns:
        cursor.execute("ALTER TABLE customers ADD COLUMN customer_type TEXT DEFAULT 'Postpaid'")
        cursor.execute("UPDATE customers SET customer_type = 'Postpaid' WHERE customer_type IS NULL")
        conn.commit()
    
    conn.close()
    
    print("✓ Application startup complete")


def require_superadmin(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse(url="/login", status_code=303)
    if request.session.get("user_type") != "superadmin":
        return RedirectResponse(url="/login", status_code=303)
    return None

def require_admin(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse(url="/login", status_code=303)
    if request.session.get("user_type") != "admin":
        return RedirectResponse(url="/login", status_code=303)
    return None

def require_employee(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse(url="/login", status_code=303)
    if request.session.get("user_type") != "employee":
        return RedirectResponse(url="/login", status_code=303)
    return None

def require_not_employee(request: Request):
    """Block employees from accessing destructive operations"""
    if request.session.get("user_type") == "employee":
        raise HTTPException(status_code=403, detail="Employees cannot perform this action")
    return None

def require_auth(request: Request):
    """Allow any authenticated user (admin or employee). Returns a redirect if not authenticated."""
    if "user_id" not in request.session:
        return RedirectResponse(url="/login", status_code=303)
    return None

def is_customer_in_employee_scope(customer, request: Request, db: Session) -> bool:
    """Return True iff the given customer belongs to one of the current employee's assigned localities.
    Non-employee sessions (admin/superadmin) always return True."""
    if request.session.get("user_type") != "employee":
        return True
    if not customer:
        return False
    from database import Employee, EmployeeLocalityAssignment, Location
    employee_code = request.session.get("user_id")
    company_id = request.session.get("company_id")
    if not employee_code or not company_id:
        return False
    if customer.company_id != company_id:
        return False
    employee = db.query(Employee).filter(
        Employee.employee_code == employee_code,
        Employee.company_id == company_id
    ).first()
    if not employee:
        return False
    assigned_location_ids_subq = db.query(EmployeeLocalityAssignment.location_id).filter(
        EmployeeLocalityAssignment.employee_id == employee.id,
        EmployeeLocalityAssignment.company_id == company_id,
        EmployeeLocalityAssignment.active == True
    ).subquery()
    location_names = db.query(Location.name).filter(
        Location.id.in_(select(assigned_location_ids_subq)),
        Location.company_id == company_id
    ).all()
    normalized = {(n[0] or "").strip().upper() for n in location_names}
    cust_loc = (customer.locality or "").strip().upper()
    return cust_loc in normalized and cust_loc != ""

def compute_customer_balance(customer_id: str, company_id: str, db: Session, exclude_invoice_no: str = None) -> float:
    """sum(invoices) - sum(payments) - sum(discounts). Can be negative for credit."""
    from database import Invoice, Payment
    invoice_query = db.query(func.sum(Invoice.total_amount)).filter(
        Invoice.customer_id == customer_id,
        Invoice.company_id == company_id
    )
    if exclude_invoice_no:
        invoice_query = invoice_query.filter(Invoice.invoice_no != exclude_invoice_no)
    total_invoices = invoice_query.scalar() or 0
    payment_sum = db.query(func.sum(Payment.amount)).filter(
        Payment.customer_id == customer_id,
        Payment.company_id == company_id
    ).scalar() or 0
    discount_sum = db.query(func.sum(Payment.discount)).filter(
        Payment.customer_id == customer_id,
        Payment.company_id == company_id
    ).scalar() or 0
    return total_invoices - payment_sum - discount_sum

def calculate_total_due(customer, db: Session) -> float:
    return compute_customer_balance(customer.customer_id, customer.company_id, db)

def format_date_ddmmyyyy(date_str: str) -> str:
    """Convert YYYY-MM-DD to DD-MM-YYYY format"""
    if not date_str:
        return ''
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        return dt.strftime('%d-%m-%Y')
    except Exception:
        return date_str if date_str else ''

def compute_tax_breakdown(company_state: str, customer_state: str, base_amount: float) -> dict:
    """Compute CGST, SGST, IGST based on state matching (GST 18%)."""
    gst_rate = 0.18
    if company_state and customer_state and company_state.lower() == customer_state.lower():
        cgst = base_amount * (gst_rate / 2)
        sgst = base_amount * (gst_rate / 2)
        igst = 0.0
    else:
        cgst = 0.0
        sgst = 0.0
        igst = base_amount * gst_rate
    return {
        'cgst_tax': round(cgst),
        'sgst_tax': round(sgst),
        'igst_tax': round(igst),
        'total_amount': round(base_amount + cgst + sgst + igst)
    }

def generate_invoice_number(company_id: str, db: Session) -> str:
    """Generate unique random 8-digit invoice number for a company"""
    from database import Invoice
    for _ in range(50):
        random_number = random.randint(10000000, 99999999)
        invoice_no = f"INV{random_number}"
        existing = db.query(Invoice).filter(
            Invoice.company_id == company_id,
            Invoice.invoice_no == invoice_no
        ).first()
        if not existing:
            return invoice_no
    raise Exception("Failed to generate unique invoice number")

def generate_transaction_no(payment_mode: str, db: Session) -> str:
    """Generate unique transaction number with retry logic"""
    prefixes = {
        'Cash': 'CSH', 'Paytm': 'PTM', 'Google Pay': 'GPY', 'Phone Pay': 'PPY',
        'Cheque': 'CHQ', 'Netbanking': 'NET', 'Online Portal': 'PRT'
    }
    from database import Payment
    prefix = prefixes.get(payment_mode, 'TXN')
    for _ in range(10):
        random_num = ''.join(random.choices(string.digits, k=6))
        transaction_no = f"{prefix}{random_num}"
        existing = db.query(Payment).filter(Payment.transaction_no == transaction_no).first()
        if not existing:
            return transaction_no
    raise Exception("Failed to generate unique transaction number")

def get_admin_context(request: Request, db: Session, active_page: str = ""):
    """Helper function to get common admin context data"""
    user_id = request.session.get("user_id", "N/A")
    user_name = request.session.get("user_name", "User")
    user_type = request.session.get("user_type", "admin")
    company_id = request.session.get("company_id", "N/A")
    
    from database import Company, Admin
    company = db.query(Company).filter(Company.company_id == company_id).first()
    company_name = company.company_name if company else "N/A"
    company_logo = company.logo_path if company and company.logo_path else None
    
    admin = db.query(Admin).filter(Admin.admin_id == user_id).first()
    admin_name = admin.admin_name if admin else user_name
    profile_image = admin.profile_image_path if admin and admin.profile_image_path else None
    
    return {
        "request": request,
        "user_id": user_id,
        "user_name": user_name,
        "admin_name": admin_name,
        "profile_image": profile_image,
        "user_type": user_type,
        "company_id": company_id,
        "company_name": company_name,
        "company_logo": company_logo,
        "active_page": active_page
    }

def get_employee_context(request: Request, db: Session, active_page: str = ""):
    from database import Company, Employee, EmployeePermission, Permission, EmployeeLocalityAssignment
    
    employee_code = request.session.get("user_id", "N/A")
    user_name = request.session.get("user_name", "User")
    company_id = request.session.get("company_id", "N/A")
    
    company = db.query(Company).filter(Company.company_id == company_id).first()
    company_name = company.company_name if company else "N/A"
    company_logo = company.logo_path if company and company.logo_path else None
    
    employee = db.query(Employee).filter(
        Employee.employee_code == employee_code,
        Employee.company_id == company_id
    ).first()
    
    employee_name = employee.employee_name if employee else user_name
    profile_image = employee.profile_image_path if employee and employee.profile_image_path else None
    employee_id = employee.id if employee else None
    
    permissions = []
    if employee:
        perms = db.query(Permission.key).join(
            EmployeePermission,
            EmployeePermission.permission_id == Permission.id
        ).filter(
            EmployeePermission.employee_id == employee.id
        ).all()
        permissions = [p.key for p in perms]
    
    assigned_location_ids = []
    if employee:
        locs = db.query(EmployeeLocalityAssignment.location_id).filter(
            EmployeeLocalityAssignment.employee_id == employee.id,
            EmployeeLocalityAssignment.active == True
        ).distinct().all()
        assigned_location_ids = [loc.location_id for loc in locs]
    
    return {
        "request": request,
        "user_id": employee_code,
        "user_name": user_name,
        "employee_name": employee_name,
        "employee_id": employee_id,
        "profile_image": profile_image,
        "user_type": "employee",
        "company_id": company_id,
        "company_name": company_name,
        "company_logo": company_logo,
        "permissions": permissions,
        "assigned_location_ids": assigned_location_ids,
        "active_page": active_page
    }

def scope_customers_to_employee(query, request: Request, db: Session):
    """Filter customer query to only include customers in employee's assigned localities"""
    from database import Location, EmployeeLocalityAssignment, Employee
    from sqlalchemy import func
    
    employee_code = request.session.get("user_id")
    company_id = request.session.get("company_id")
    
    if not employee_code or not company_id:
        return query.filter(text("1=0"))
    
    employee = db.query(Employee).filter(
        Employee.employee_code == employee_code,
        Employee.company_id == company_id
    ).first()
    
    if not employee:
        return query.filter(text("1=0"))
    
    assigned_location_ids_subq = db.query(EmployeeLocalityAssignment.location_id).filter(
        EmployeeLocalityAssignment.employee_id == employee.id,
        EmployeeLocalityAssignment.company_id == company_id,
        EmployeeLocalityAssignment.active == True
    ).subquery()
    
    location_names = db.query(Location.name).filter(
        Location.id.in_(select(assigned_location_ids_subq)),
        Location.company_id == company_id
    ).all()
    
    if not location_names:
        return query.filter(text("1=0"))
    
    normalized_names = [name[0].strip().upper() for name in location_names if name[0]]
    
    if not normalized_names:
        return query.filter(text("1=0"))
    
    return query.filter(func.upper(func.trim(Customer.locality)).in_(normalized_names))


async def homepage(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# --- S38: Employee permission enforcement helpers  __S38_PERM_ENFORCED__ ---
def employee_has_permission(request, db, key: str) -> bool:
    """True if the current session holds the permission key.
    Admins / superadmins always pass.  Employees are matched against the
    employee_permissions × permissions join."""
    ut = request.session.get("user_type")
    if ut in ("admin", "superadmin"):
        return True
    if ut != "employee":
        return False
    try:
        from database import Employee, EmployeePermission, Permission
    except Exception:
        return False
    emp_code = request.session.get("user_id")
    cid = request.session.get("company_id")
    if not emp_code or not cid:
        return False
    emp = db.query(Employee).filter(
        Employee.employee_code == emp_code,
        Employee.company_id == cid,
    ).first()
    if not emp:
        return False
    hit = db.query(Permission.id).join(
        EmployeePermission, EmployeePermission.permission_id == Permission.id
    ).filter(
        EmployeePermission.employee_id == emp.id,
        Permission.key == key,
    ).first()
    return bool(hit)


def require_permission_page(request, db, key: str):
    """For HTML routes — returns a RedirectResponse to the dashboard with a
    ?denied=<key> flash when the employee lacks the permission, else None."""
    if employee_has_permission(request, db, key):
        return None
    return RedirectResponse(url=f"/employee/dashboard?denied={key}", status_code=303)


def require_permission_api(request, db, key: str):
    """For JSON API routes — returns a 403 JSONResponse when denied, else None."""
    if employee_has_permission(request, db, key):
        return None
    return JSONResponse(
        {"success": False, "message": f"Permission denied: {key}"},
        status_code=403,
    )
# --- /S38 ---

@app.get("/employee/login", response_class=HTMLResponse)
async def employee_login_alias(request: Request):
    """Alias route for /employee/login -> /login"""
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/api/auth/login")
async def login(
    request: Request,
    userType: str = Form(...),
    userId: str = Form(...),
    password: str = Form(...),
    companyId: str = Form(None),
    db: Session = Depends(get_db)
):
    user = None
    
    if userType == "superadmin":
        user = authenticate_superadmin(db, userId, password)
        if user:
            request.session["user_id"] = user.superadmin_id
            request.session["user_type"] = "superadmin"
            request.session["user_name"] = user.superadmin_name or "Super Administrator"
            request.session["superadmin_id"] = user.id
            return JSONResponse({"success": True, "redirect": "/superadmin/dashboard"})
    
    elif userType == "admin":
        if not companyId:
            return JSONResponse({"success": False, "message": "Company ID is required for Admin login"}, status_code=400)
        user = authenticate_admin(db, companyId, userId, password)
        if user:
            request.session["user_id"] = user.admin_id
            request.session["user_type"] = "admin"
            request.session["user_name"] = user.admin_name
            request.session["company_id"] = user.company_id
            return JSONResponse({"success": True, "redirect": "/admin/dashboard"})
    
    elif userType == "employee":
        if not companyId:
            return JSONResponse({"success": False, "message": "Company ID is required for Employee login"}, status_code=400)
        user = authenticate_employee(db, companyId, userId, password)
        if user:
            request.session["user_id"] = user.employee_code
            request.session["user_type"] = "employee"
            request.session["user_name"] = user.employee_name
            request.session["company_id"] = user.company_id
            request.session["employee_id"] = user.id
            return JSONResponse({"success": True, "redirect": "/employee/dashboard"})
    
    elif userType == "customer":
        user = authenticate_customer(db, userId, password)
        if user:
            request.session["user_id"] = user.customer_id
            request.session["user_type"] = "customer"
            request.session["user_name"] = user.customer_name
            return JSONResponse({"success": True, "redirect": "/customer/dashboard"})
    
    return JSONResponse({"success": False, "message": "Invalid credentials"}, status_code=401)

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login")


@app.get("/api/employees/list")
async def list_employees(request: Request, db: Session = Depends(get_db)):
    """Get employees list for dropdown"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    admin_id = request.session.get("user_id")
    
    from database import Employee, Admin
    
    try:
        employees = db.query(Employee).filter(
            Employee.company_id == company_id,
            Employee.is_deleted == False
        ).all()
        
        employee_list = []
        
        if not employees:
            admin = db.query(Admin).filter(Admin.admin_id == admin_id).first()
            if admin:
                employee_list.append({
                    "id": admin.admin_id,
                    "employee_id": admin.admin_id,
                    "employee_code": f"ADMIN{admin.admin_id}",
                    "employee_name": f"{admin.admin_name} (Admin)"
                })
        else:
            for emp in employees:
                employee_list.append({
                    "id": emp.id,  # Numeric ID for form submission
                    "employee_id": emp.employee_code,  # For backward compatibility
                    "employee_code": emp.employee_code,  # For display
                    "employee_name": emp.employee_name
                })
        
        return {"success": True, "employees": employee_list}
    
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/employees/datatable")
async def get_employees_datatable(request: Request, db: Session = Depends(get_db)):
    """Get employees for DataTables with server-side processing"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Employee
    from sqlalchemy import or_, func
    
    try:
        draw = int(request.query_params.get('draw', 1))
        start = int(request.query_params.get('start', 0))
        length = int(request.query_params.get('length', 10))
        search_value = request.query_params.get('search[value]', '').strip()
        status_filter = request.query_params.get('status', '').strip()
        letter_filter = request.query_params.get('letter', '').strip()
        
        query = db.query(Employee).filter(
            Employee.company_id == company_id,
            Employee.is_deleted == False
        )
        
        if status_filter and status_filter != 'All':
            query = query.filter(Employee.status == status_filter)
        
        if letter_filter and letter_filter != 'All':
            query = query.filter(func.upper(func.substr(Employee.employee_name, 1, 1)) == letter_filter.upper())
        
        if search_value:
            query = query.filter(
                or_(
                    Employee.employee_name.ilike(f'%{search_value}%'),
                    Employee.employee_code.ilike(f'%{search_value}%'),
                    Employee.mobile.ilike(f'%{search_value}%'),
                    Employee.email.ilike(f'%{search_value}%')
                )
            )
        
        total_records = db.query(Employee).filter(
            Employee.company_id == company_id,
            Employee.is_deleted == False
        ).count()
        
        filtered_records = query.count()
        
        employees = query.order_by(Employee.employee_name).offset(start).limit(length).all()
        
        data = []
        for idx, emp in enumerate(employees, start=start+1):
            data.append({
                "sno": idx,
                "id": emp.id,
                "employee_name": emp.employee_name,
                "employee_code": emp.employee_code,
                "mobile": emp.mobile or "",
                "email": emp.email or "",
                "address": emp.address or "",
                "status": emp.status,
                "profile_image": emp.profile_image_path or ""
            })
        
        return {
            "draw": draw,
            "recordsTotal": total_records,
            "recordsFiltered": filtered_records,
            "data": data
        }
    
    except Exception as e:
        return {"draw": 1, "recordsTotal": 0, "recordsFiltered": 0, "data": [], "error": str(e)}

@app.get("/api/permissions/list")
async def get_permissions(db: Session = Depends(get_db)):
    """Get all permissions grouped by category (public endpoint - returns static metadata only)"""
    from database import Permission
    
    try:
        permissions = db.query(Permission).order_by(Permission.category, Permission.label).all()
        
        grouped = {
            "feature": [],
            "app": [],
            "report": []
        }
        
        for perm in permissions:
            grouped[perm.category].append({
                "id": perm.id,
                "key": perm.key,
                "label": perm.label,
                "description": perm.description
            })
        
        return {"success": True, "permissions": grouped}
    
    except Exception as e:
        return {"success": False, "message": str(e)}

def get_company_prefix(company_name: str) -> str:
    """Extract 3-letter prefix from company name"""
    if not company_name:
        return "CMP"
    
    name = company_name.strip()
    
    letters = ''.join(c for c in name if c.isalpha())
    
    if len(letters) >= 3:
        return letters[:3].upper()
    elif len(letters) > 0:
        return (letters + "XXX")[:3].upper()
    else:
        return "CMP"

def generate_employee_code(company_id: str, db: Session) -> str:
    """Generate unique employee code for company (format: PREFIX + random 8-digit number)"""
    import random
    from database import Employee, Company
    
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise ValueError(f"Company {company_id} not found")
    
    prefix = get_company_prefix(company.company_name)
    
    # Generate random 8-digit number (10000000 to 99999999)
    for _ in range(100):
        random_number = random.randint(10000000, 99999999)
        employee_code = f"{prefix}{random_number}"
        
        existing = db.query(Employee).filter(Employee.employee_code == employee_code).first()
        if not existing:
            return employee_code
    
    raise ValueError(f"Could not generate unique employee code for company {company_id}")

@app.post("/api/employees/create")
async def create_employee(request: Request, db: Session = Depends(get_db)):
    """Create a new employee"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    admin_id = request.session.get("user_id")
    
    from database import Employee, EmployeePermission
    import bcrypt
    
    try:
        data = await request.json()
        
        employee_name = data.get('employee_name', '').strip()
        password = data.get('password', '').strip()
        mobile = data.get('mobile', '').strip()
        email = data.get('email', '').strip()
        address = data.get('address', '').strip()
        permission_ids = data.get('permissions', [])
        
        if not employee_name or not password or not mobile:
            return {"success": False, "message": "Employee name, password, and mobile are required"}
        
        employee_code = generate_employee_code(company_id, db)
        
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        new_employee = Employee(
            company_id=company_id,
            employee_code=employee_code,
            employee_name=employee_name,
            password_hash=password_hash,
            mobile=mobile,
            email=email if email else None,
            address=address if address else None,
            status='Active',
            created_by=admin_id
        )
        
        db.add(new_employee)
        db.flush()
        
        for perm_id in permission_ids:
            emp_perm = EmployeePermission(
                employee_id=new_employee.id,
                permission_id=int(perm_id),
                granted_by=admin_id
            )
            db.add(emp_perm)
        
        db.commit()
        
        return {
            "success": True,
            "message": "Employee created successfully",
            "employee_code": employee_code,
            "employee_id": new_employee.id
        }
    
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

@app.get("/api/employees/{employee_id}")
async def get_employee(employee_id: int, request: Request, db: Session = Depends(get_db)):
    """Get employee details with permissions"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Employee, EmployeePermission
    
    try:
        employee = db.query(Employee).filter(
            Employee.id == employee_id,
            Employee.company_id == company_id,
            Employee.is_deleted == False
        ).first()
        
        if not employee:
            return {"success": False, "message": "Employee not found"}
        
        permissions = db.query(EmployeePermission.permission_id).filter(
            EmployeePermission.employee_id == employee_id
        ).all()
        
        permission_ids = [p[0] for p in permissions]
        
        return {
            "success": True,
            "employee": {
                "id": employee.id,
                "employee_code": employee.employee_code,
                "employee_name": employee.employee_name,
                "mobile": employee.mobile,
                "email": employee.email or "",
                "address": employee.address or "",
                "status": employee.status,
                "profile_image": employee.profile_image_path or "",
                "permissions": permission_ids
            }
        }
    
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.put("/api/employees/{employee_id}")
async def update_employee(employee_id: int, request: Request, db: Session = Depends(get_db)):
    """Update employee details and permissions"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    admin_id = request.session.get("user_id")
    
    from database import Employee, EmployeePermission
    import bcrypt
    
    try:
        employee = db.query(Employee).filter(
            Employee.id == employee_id,
            Employee.company_id == company_id,
            Employee.is_deleted == False
        ).first()
        
        if not employee:
            return {"success": False, "message": "Employee not found"}
        
        data = await request.json()
        
        employee.employee_name = data.get('employee_name', employee.employee_name).strip()
        employee.mobile = data.get('mobile', employee.mobile).strip()
        employee.email = data.get('email', '').strip() or None
        employee.address = data.get('address', '').strip() or None
        employee.updated_by = admin_id
        
        if data.get('password'):
            password = data['password'].strip()
            employee.password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        db.query(EmployeePermission).filter(EmployeePermission.employee_id == employee_id).delete()
        
        permission_ids = data.get('permissions', [])
        for perm_id in permission_ids:
            emp_perm = EmployeePermission(
                employee_id=employee_id,
                permission_id=int(perm_id),
                granted_by=admin_id
            )
            db.add(emp_perm)
        
        db.commit()
        
        return {"success": True, "message": "Employee updated successfully"}
    
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

@app.post("/api/employees/{employee_id}/toggle-status")
async def toggle_employee_status(employee_id: int, request: Request, db: Session = Depends(get_db)):
    """Toggle employee active/deactive status"""
    auth_check = require_admin(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    admin_id = request.session.get("user_id")
    
    from database import Employee
    
    try:
        employee = db.query(Employee).filter(
            Employee.id == employee_id,
            Employee.company_id == company_id,
            Employee.is_deleted == False
        ).first()
        
        if not employee:
            return {"success": False, "message": "Employee not found"}
        
        employee.status = 'Deactive' if employee.status == 'Active' else 'Active'
        employee.updated_by = admin_id
        db.commit()
        
        return {
            "success": True,
            "message": f"Employee {employee.status.lower()}d successfully",
            "status": employee.status
        }
    
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

@app.delete("/api/employees/{employee_id}")
async def delete_employee(employee_id: int, request: Request, db: Session = Depends(get_db), _ = Depends(require_not_employee)):
    """Soft delete employee"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    admin_id = request.session.get("user_id")
    
    from database import Employee
    
    try:
        employee = db.query(Employee).filter(
            Employee.id == employee_id,
            Employee.company_id == company_id,
            Employee.is_deleted == False
        ).first()
        
        if not employee:
            return {"success": False, "message": "Employee not found"}
        
        employee.is_deleted = True
        employee.updated_by = admin_id
        db.commit()
        
        return {"success": True, "message": "Employee deleted successfully"}
    
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

@app.get("/api/employee-locations")
async def get_all_employee_locations(request: Request, db: Session = Depends(get_db)):
    """Get all employee locations for GPS tracking map"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Employee
    
    try:
        employees = db.query(Employee).filter(
            Employee.company_id == company_id,
            Employee.status == 'Active',
            Employee.is_deleted == False
        ).all()
        
        locations = []
        for emp in employees:
            locations.append({
                "id": emp.id,
                "employee_code": emp.employee_code,
                "employee_name": emp.employee_name,
                "mobile": emp.mobile,
                "status": emp.status,
                "last_latitude": emp.last_latitude,
                "last_longitude": emp.last_longitude,
                "last_seen_at": emp.last_seen_at.isoformat() if emp.last_seen_at else None
            })
        
        return {"success": True, "locations": locations}
    
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/employee", response_class=HTMLResponse)
async def employee_root(request: Request):
    """Redirect /employee to /employee/dashboard"""
    return RedirectResponse(url="/employee/dashboard", status_code=303)

@app.get("/employee/dashboard", response_class=HTMLResponse)
async def employee_dashboard(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    
    from database import Customer, Complaint, Invoice, Payment
    company_id = request.session.get("company_id")
    
    base_customers = db.query(Customer).filter(
        Customer.company_id == company_id,
        Customer.status != "Deleted"
    )
    scoped_customers = scope_customers_to_employee(base_customers, request, db)
    
    scoped_ids = [c.customer_id for c in scoped_customers.all()]
    total_customers = len(scoped_ids)
    active_customers = len([c for c in scoped_customers.all() if c.status == "Active"])
    
    open_complaints = 0
    pending_payments = 0
    if scoped_ids:
        open_complaints = db.query(Complaint).filter(
            Complaint.company_id == company_id,
            Complaint.customer_id.in_(scoped_ids),
            Complaint.status.in_(["Open", "In Progress"])
        ).count()
        total_invoices = db.query(func.sum(Invoice.total_amount)).filter(
            Invoice.company_id == company_id,
            Invoice.customer_id.in_(scoped_ids)
        ).scalar() or 0
        total_paid = db.query(func.sum(Payment.amount)).filter(
            Payment.company_id == company_id,
            Payment.customer_id.in_(scoped_ids)
        ).scalar() or 0
        pending_payments = max(0, float(total_invoices) - float(total_paid))
    
    context = get_employee_context(request, db, "dashboard")
    context["stats"] = {
        "total_customers": total_customers,
        "active_customers": active_customers,
        "open_complaints": open_complaints,
        "pending_payments": f"{pending_payments:,.0f}"
    }
    return templates.TemplateResponse("employee_dashboard.html", context)

@app.get("/employee/users", response_class=HTMLResponse)
async def employee_users(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    _p = require_permission_page(request, db, "feat.user_management")
    if _p:
        return _p
    
    context = get_employee_context(request, db, "users")
    company_id = request.session.get("company_id")
    
    from database import Customer, Payment, Plan, Invoice, ReceivedTracker
    from datetime import datetime, timedelta
    
    base_query = db.query(Customer).filter(
        Customer.company_id == company_id,
        Customer.status != "Deleted"
    )
    
    filtered_query = scope_customers_to_employee(base_query, request, db)
    customers = filtered_query.all()
    
    payment_sums = {}
    discount_sums = {}
    invoice_sums = {}
    received_since_reset = {}
    
    payment_data = db.query(
        Payment.customer_id,
        func.sum(Payment.amount).label('total_amount'),
        func.sum(Payment.discount).label('total_discount')
    ).filter(
        Payment.company_id == company_id
    ).group_by(Payment.customer_id).all()
    
    for row in payment_data:
        payment_sums[row.customer_id] = row.total_amount or 0
        discount_sums[row.customer_id] = row.total_discount or 0
    
    invoice_data = db.query(
        Invoice.customer_id,
        func.sum(Invoice.total_amount).label('total_invoices')
    ).filter(
        Invoice.company_id == company_id
    ).group_by(Invoice.customer_id).all()
    
    for row in invoice_data:
        invoice_sums[row.customer_id] = row.total_invoices or 0
    
    tracker_data = db.query(ReceivedTracker).filter(
        ReceivedTracker.company_id == company_id
    ).all()
    
    for tracker in tracker_data:
        received_since_reset[tracker.customer_id] = tracker.received_since_reset or 0
    
    plans = {p.id: p for p in db.query(Plan).filter(Plan.company_id == company_id).all()}
    
    users = []
    for customer in customers:
        plan_obj = plans.get(customer.plan_id) if customer.plan_id else None
        plan_name = plan_obj.plan_name if plan_obj else "N/A"
        
        if customer.monthly_amount:
            amount_display = customer.monthly_amount
        else:
            amount_display = plan_obj.after_tax_amount if plan_obj else 0
        
        total_invoices = invoice_sums.get(customer.customer_id, 0)
        received_amount = payment_sums.get(customer.customer_id, 0)
        discount_amount = discount_sums.get(customer.customer_id, 0)
        received_since_last_invoice = received_since_reset.get(customer.customer_id, 0)
        
        balance = total_invoices - received_amount - discount_amount
        
        if customer.end_date:
            try:
                end_dt = datetime.strptime(customer.end_date, '%Y-%m-%d')
                display_end_dt = end_dt - timedelta(days=1)
                exp_date = format_date_ddmmyyyy(display_end_dt.strftime('%Y-%m-%d'))
            except:
                exp_date = format_date_ddmmyyyy(customer.end_date)
        else:
            exp_date = ""
        
        address_full = customer.address or ""
        address_line = address_full.split('\n')[0] if address_full else (customer.locality or "")
        
        users.append({
            "cust_id": customer.customer_id,
            "cust_name": customer.customer_name,
            "user_name": customer.username,
            "address": address_line,
            "mobile": customer.customer_phone,
            "status": customer.status,
            "plan": plan_name,
            "amount": f"{amount_display:,.0f}" if amount_display else "0",
            "received": f"{received_since_last_invoice:,.0f}",
            "balance": f"{balance:,.0f}",
            "exp_date": exp_date
        })
    
    context["users"] = users
    return templates.TemplateResponse("employee_users.html", context)

@app.get("/employee/plans", response_class=HTMLResponse)
async def employee_plans(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    
    from database import Plan
    
    company_id = request.session.get("company_id")
    
    # Fetch plans filtered by company_id
    plans = db.query(Plan).filter(Plan.company_id == company_id).all()
    
    context = get_employee_context(request, db, "plans")
    context["plans"] = plans
    return templates.TemplateResponse("employee_plans.html", context)

@app.get("/employee/locations", response_class=HTMLResponse)
async def employee_locations(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check

    from database import Location
    company_id = request.session.get("company_id")
    locations = db.query(Location).filter(Location.company_id == company_id).order_by(Location.name).all()

    context = get_employee_context(request, db, "locations")
    context["locations"] = locations
    return templates.TemplateResponse("employee_locations.html", context)

@app.get("/employee/transactions", response_class=HTMLResponse)
async def employee_transactions(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    _p = require_permission_page(request, db, "feat.transaction_history")
    if _p:
        return _p

    context = get_employee_context(request, db, "transactions")
    return templates.TemplateResponse("employee_transactions.html", context)

@app.get("/employee/addon-bills", response_class=HTMLResponse)
async def employee_addon_bills(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    _p = require_permission_page(request, db, "feat.invoice")
    if _p:
        return _p

    from database import Customer, Plan
    company_id = request.session.get("company_id")
    base_q = db.query(Customer).filter(
        Customer.company_id == company_id,
        Customer.status != "Deleted",
    )
    customers = scope_customers_to_employee(base_q, request, db).order_by(Customer.customer_name).all()
    plans = db.query(Plan).filter(Plan.company_id == company_id).all()

    context = get_employee_context(request, db, "addon-bills")
    context["customers"] = customers
    context["plans"] = plans
    return templates.TemplateResponse("employee_addon_bills.html", context)

@app.get("/employee/send-invoices", response_class=HTMLResponse)
async def employee_send_invoices(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    _p = require_permission_page(request, db, "feat.invoice")
    if _p:
        return _p

    from database import Customer
    company_id = request.session.get("company_id")
    base_q = db.query(Customer).filter(
        Customer.company_id == company_id,
        Customer.status != "Deleted",
    )
    customers = scope_customers_to_employee(base_q, request, db).order_by(Customer.customer_name).all()

    context = get_employee_context(request, db, "send-invoices")
    context["customers"] = customers
    return templates.TemplateResponse("employee_send_invoices.html", context)

@app.get("/employee/complaints", response_class=HTMLResponse)
async def employee_complaints(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    _p = require_permission_page(request, db, "feat.complaints")
    if _p:
        return _p
    context = get_employee_context(request, db, "complaints")
    return templates.TemplateResponse("employee_complaints.html", context)

@app.get("/employee/notifications", response_class=HTMLResponse)
async def employee_notifications(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    _p = require_permission_page(request, db, "feat.notification")
    if _p:
        return _p
    from database import Notification
    notifications = db.query(Notification).order_by(Notification.created_at.desc()).limit(100).all()
    context = get_employee_context(request, db, "notifications")
    context["notifications"] = notifications
    return templates.TemplateResponse("employee_notifications.html", context)

@app.get("/employee/reports", response_class=HTMLResponse)
async def employee_reports(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    _p = require_permission_page(request, db, "feat.reports")
    if _p:
        return _p
    context = get_employee_context(request, db, "reports")
    return templates.TemplateResponse("employee_reports.html", context)

@app.get("/employee/sms-logs", response_class=HTMLResponse)
async def employee_sms_logs(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    _p = require_permission_page(request, db, "feat.notification")
    if _p:
        return _p
    context = get_employee_context(request, db, "sms-logs")
    return templates.TemplateResponse("employee_sms_logs.html", context)

@app.get("/employee/data-management", response_class=HTMLResponse)
async def employee_data_management(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    _p = require_permission_page(request, db, "feat.data_management")
    if _p:
        return _p
    context = get_employee_context(request, db, "data-management")
    return templates.TemplateResponse("employee_data_management.html", context)

@app.get("/employee/deleted-users", response_class=HTMLResponse)
async def employee_deleted_users(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    _p = require_permission_page(request, db, "feat.user_management")
    if _p:
        return _p
    context = get_employee_context(request, db, "deleted-users")
    return templates.TemplateResponse("employee_deleted_users.html", context)

@app.get("/employee/connection-request", response_class=HTMLResponse)
async def employee_connection_request(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    _p = require_permission_page(request, db, "feat.user_management")
    if _p:
        return _p
    context = get_employee_context(request, db, "connection-request")
    return templates.TemplateResponse("employee_connection_request.html", context)

@app.get("/employee/book-connection", response_class=HTMLResponse)
async def employee_book_connection(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    _p = require_permission_page(request, db, "feat.user_management")
    if _p:
        return _p

    from database import Plan, Location
    company_id = request.session.get("company_id")
    plans = db.query(Plan).filter(Plan.company_id == company_id).all()
    locations = db.query(Location).filter(Location.company_id == company_id).order_by(Location.name).all()

    context = get_employee_context(request, db, "book-connection")
    context["plans"] = plans
    context["locations"] = locations
    return templates.TemplateResponse("employee_book_connection.html", context)


# ============================================================================
# Customer-scope helpers
# ============================================================================

def _get_scoped_customer(customer_id: str, request: Request, db: Session):
    """Return the Customer for the current company_id iff it is in the employee's scope.
    Returns None if not found or out of scope."""
    company_id = request.session.get("company_id")
    if not company_id:
        return None
    customer = db.query(Customer).filter(
        Customer.customer_id == customer_id,
        Customer.company_id == company_id,
    ).first()
    if not customer:
        return None
    if not is_customer_in_employee_scope(customer, request, db):
        return None
    return customer


# ============================================================================
# /api/locations/*
# ============================================================================

@app.get("/api/locations/list")
async def api_locations_list(request: Request, service_type: str = None, db: Session = Depends(get_db)):
    """List locations for current company, optionally filtered by service_type (Broadband/Cable)."""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    company_id = request.session.get("company_id")
    from database import Location

    try:
        query = db.query(Location).filter(Location.company_id == company_id)
        if service_type:
            query = query.filter(or_(Location.service_type == service_type, Location.service_type.is_(None)))
        locations = query.order_by(Location.name).all()

        return {
            "success": True,
            "locations": [
                {
                    "id": loc.id,
                    "name": loc.name,
                    "service_type": loc.service_type,
                }
                for loc in locations
            ],
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/locations/create")
async def api_locations_create(request: Request, db: Session = Depends(get_db)):
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    company_id = request.session.get("company_id")
    from database import Location

    try:
        try:
            data = await request.json()
        except Exception:
            form = await request.form()
            data = dict(form)

        name = (data.get("name") or "").strip()
        service_type = (data.get("service_type") or "").strip() or None

        if not name:
            return {"success": False, "message": "Location name is required"}

        existing = db.query(Location).filter(
            Location.company_id == company_id,
            func.upper(func.trim(Location.name)) == name.strip().upper(),
        ).first()
        if existing:
            return {"success": False, "message": "Location with the same name already exists"}

        loc = Location(
            company_id=company_id,
            name=name,
            service_type=service_type,
        )
        db.add(loc)
        db.commit()
        db.refresh(loc)
        return {"success": True, "message": "Location created", "id": loc.id}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}


@app.post("/api/locations/update")
async def api_locations_update(request: Request, db: Session = Depends(get_db)):
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    company_id = request.session.get("company_id")
    from database import Location

    try:
        try:
            data = await request.json()
        except Exception:
            form = await request.form()
            data = dict(form)

        loc_id = data.get("id") or data.get("location_id")
        if not loc_id:
            return {"success": False, "message": "Location ID is required"}

        loc = db.query(Location).filter(
            Location.id == int(loc_id),
            Location.company_id == company_id,
        ).first()
        if not loc:
            return {"success": False, "message": "Location not found"}

        if "name" in data and data.get("name"):
            loc.name = data["name"].strip()
        if "service_type" in data:
            loc.service_type = (data.get("service_type") or "").strip() or None

        db.commit()
        return {"success": True, "message": "Location updated"}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}


@app.post("/api/locations/delete")
async def api_locations_delete(request: Request, db: Session = Depends(get_db)):
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    company_id = request.session.get("company_id")
    from database import Location

    try:
        try:
            data = await request.json()
        except Exception:
            form = await request.form()
            data = dict(form)

        loc_id = data.get("id") or data.get("location_id")
        if not loc_id:
            return {"success": False, "message": "Location ID is required"}

        loc = db.query(Location).filter(
            Location.id == int(loc_id),
            Location.company_id == company_id,
        ).first()
        if not loc:
            return {"success": False, "message": "Location not found"}

        db.delete(loc)
        db.commit()
        return {"success": True, "message": "Location deleted"}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}


# ============================================================================
# /api/plans/*
# ============================================================================

@app.get("/api/plans/list")
async def api_plans_list(request: Request, service: str = None, db: Session = Depends(get_db)):
    auth_check = require_auth(request)
    if auth_check:
        return {"error": "Unauthorized"}

    company_id = request.session.get("company_id")
    from database import Plan

    query = db.query(Plan).filter(Plan.company_id == company_id)
    if service:
        query = query.filter(Plan.service == service)
    plans = query.order_by(Plan.plan_name).all()

    return {
        "plans": [
            {
                "id": p.id,
                "plan_name": p.plan_name,
                "speed": p.speed,
                "service": p.service,
                "validity": p.validity,
                "base_amount": p.base_amount,
                "cgst_tax": p.cgst_tax,
                "sgst_tax": p.sgst_tax,
                "igst_tax": p.igst_tax,
                "after_tax_amount": p.after_tax_amount,
                "description": p.description,
            }
            for p in plans
        ]
    }


@app.get("/api/plans/{plan_id}")
async def api_plan_get(plan_id: int, request: Request, db: Session = Depends(get_db)):
    auth_check = require_auth(request)
    if auth_check:
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)

    company_id = request.session.get("company_id")
    from database import Plan
    plan = db.query(Plan).filter(Plan.id == plan_id, Plan.company_id == company_id).first()
    if not plan:
        return JSONResponse({"success": False, "message": "Plan not found"}, status_code=404)

    return {
        "id": plan.id,
        "service": plan.service,
        "plan_name": plan.plan_name,
        "speed": plan.speed,
        "validity": plan.validity,
        "base_amount": round(plan.base_amount) if plan.base_amount else 0,
        "cgst_tax": plan.cgst_tax,
        "sgst_tax": plan.sgst_tax,
        "igst_tax": plan.igst_tax,
        "after_tax_amount": round(plan.after_tax_amount) if plan.after_tax_amount else 0,
        "description": plan.description,
    }


def _compute_after_tax(base_amount: float, cgst_tax: float, sgst_tax: float, igst_tax: float) -> float:
    if cgst_tax > 0 or sgst_tax > 0:
        cgst_amount = (base_amount * cgst_tax) / 100
        sgst_amount = (base_amount * sgst_tax) / 100
        return round(base_amount + cgst_amount + sgst_amount)
    igst_amount = (base_amount * igst_tax) / 100
    return round(base_amount + igst_amount)


@app.post("/api/plans")
async def api_plans_create(request: Request, db: Session = Depends(get_db)):
    """Employees may only list plans (read). Creation requires admin."""
    if request.session.get("user_type") == "employee":
        return JSONResponse({"success": False, "message": "Employees cannot create plans"}, status_code=403)
    auth_check = require_auth(request)
    if auth_check:
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)

    company_id = request.session.get("company_id")
    from database import Plan

    try:
        data = await request.json()
        base_amount = float(data.get("base_amount", 0))
        cgst_tax = float(data.get("cgst_tax", 0))
        sgst_tax = float(data.get("sgst_tax", 0))
        igst_tax = float(data.get("igst_tax", 0))
        after_tax_amount = _compute_after_tax(base_amount, cgst_tax, sgst_tax, igst_tax)

        validity_str = str(data.get("validity", "30"))
        validity = 30 if "month" in validity_str.lower() else int(validity_str)

        new_plan = Plan(
            company_id=company_id,
            service=data.get("service"),
            plan_name=data.get("plan_name"),
            speed=data.get("speed"),
            validity=validity,
            base_amount=base_amount,
            cgst_tax=cgst_tax,
            sgst_tax=sgst_tax,
            igst_tax=igst_tax,
            after_tax_amount=after_tax_amount,
            description=data.get("description", ""),
        )
        db.add(new_plan)
        db.commit()
        db.refresh(new_plan)
        return JSONResponse({"success": True, "message": "Plan saved successfully", "plan_id": new_plan.id})
    except Exception as e:
        db.rollback()
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.put("/api/plans/{plan_id}")
async def api_plans_update(plan_id: int, request: Request, db: Session = Depends(get_db)):
    if request.session.get("user_type") == "employee":
        return JSONResponse({"success": False, "message": "Employees cannot update plans"}, status_code=403)
    auth_check = require_auth(request)
    if auth_check:
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)

    company_id = request.session.get("company_id")
    from database import Plan

    try:
        plan = db.query(Plan).filter(Plan.id == plan_id, Plan.company_id == company_id).first()
        if not plan:
            return JSONResponse({"success": False, "message": "Plan not found"}, status_code=404)

        data = await request.json()
        base_amount = float(data.get("base_amount", plan.base_amount))
        cgst_tax = float(data.get("cgst_tax", plan.cgst_tax))
        sgst_tax = float(data.get("sgst_tax", plan.sgst_tax))
        igst_tax = float(data.get("igst_tax", plan.igst_tax))
        after_tax_amount = _compute_after_tax(base_amount, cgst_tax, sgst_tax, igst_tax)

        validity_str = str(data.get("validity", plan.validity))
        validity = 30 if "month" in validity_str.lower() else int(validity_str)

        plan.service = data.get("service", plan.service)
        plan.plan_name = data.get("plan_name", plan.plan_name)
        plan.speed = data.get("speed", plan.speed)
        plan.validity = validity
        plan.base_amount = base_amount
        plan.cgst_tax = cgst_tax
        plan.sgst_tax = sgst_tax
        plan.igst_tax = igst_tax
        plan.after_tax_amount = after_tax_amount
        plan.description = data.get("description", plan.description)
        db.commit()
        return JSONResponse({"success": True, "message": "Plan updated successfully"})
    except Exception as e:
        db.rollback()
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.delete("/api/plans/{plan_id}")
async def api_plans_delete(plan_id: int, request: Request, db: Session = Depends(get_db)):
    if request.session.get("user_type") == "employee":
        return JSONResponse({"success": False, "message": "Employees cannot delete plans"}, status_code=403)
    auth_check = require_auth(request)
    if auth_check:
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)

    company_id = request.session.get("company_id")
    from database import Plan
    try:
        plan = db.query(Plan).filter(Plan.id == plan_id, Plan.company_id == company_id).first()
        if not plan:
            return JSONResponse({"success": False, "message": "Plan not found"}, status_code=404)
        db.delete(plan)
        db.commit()
        return JSONResponse({"success": True, "message": "Plan deleted successfully"})
    except Exception as e:
        db.rollback()
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


# ============================================================================
# /api/customers/*
# ============================================================================

@app.get("/api/customers/check-username")
async def api_customers_check_username(username: str, request: Request, db: Session = Depends(get_db)):
    auth_check = require_auth(request)
    if auth_check:
        return {"exists": False, "message": "Unauthorized"}
    company_id = request.session.get("company_id")
    try:
        existing = db.query(Customer).filter(
            Customer.username == username,
            Customer.company_id == company_id,
        ).first()
        return {"exists": existing is not None}
    except Exception as e:
        return {"exists": False, "error": str(e)}


@app.get("/api/customers/deleted")
async def api_customers_deleted(request: Request, db: Session = Depends(get_db)):
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    company_id = request.session.get("company_id")
    try:
        base_q = db.query(Customer).filter(
            Customer.company_id == company_id,
            Customer.status == "Deleted",
        )
        deleted_customers = scope_customers_to_employee(base_q, request, db).all()

        customers_list = []
        for c in deleted_customers:
            try:
                balance = calculate_total_due(c, db)
            except Exception:
                balance = 0
            customers_list.append({
                "cust_id": c.customer_id,
                "cust_name": c.customer_name,
                "mobile": c.customer_phone,
                "address": c.address or "",
                "plan": c.plan_id,
                "amount": float(c.monthly_amount) if c.monthly_amount else 0,
                "balance": balance,
                "exp_date": c.end_date or "N/A",
            })
        return {"success": True, "customers": customers_list}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/customers/{customer_id}/restore")
async def api_customers_restore(customer_id: str, request: Request, db: Session = Depends(get_db)):
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    company_id = request.session.get("company_id")
    try:
        customer = db.query(Customer).filter(
            Customer.customer_id == customer_id,
            Customer.company_id == company_id,
        ).first()
        if not customer:
            return {"success": False, "message": "Customer not found"}
        if not is_customer_in_employee_scope(customer, request, db):
            return {"success": False, "message": "Out of scope"}
        if customer.status != "Deleted":
            return {"success": False, "message": "Customer is not deleted"}
        customer.status = "Deactive"
        db.commit()
        return {"success": True, "message": f"Customer {customer.customer_name} has been restored successfully"}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": f"Error restoring customer: {str(e)}"}


@app.delete("/api/customers/{customer_id}/permanent")
async def api_customers_permanent_delete(customer_id: str, request: Request, db: Session = Depends(get_db), _=Depends(require_not_employee)):
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    company_id = request.session.get("company_id")
    try:
        customer = db.query(Customer).filter(
            Customer.customer_id == customer_id,
            Customer.company_id == company_id,
            Customer.status == "Deleted",
        ).first()
        if not customer:
            return {"success": False, "message": "Deleted customer not found."}
        name = customer.customer_name
        db.delete(customer)
        db.commit()
        return {"success": True, "message": f"Customer {name} (ID: {customer_id}) has been permanently deleted"}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}


@app.get("/api/customers/{customer_id}")
async def api_customers_get(customer_id: str, request: Request, db: Session = Depends(get_db)):
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    customer = _get_scoped_customer(customer_id, request, db)
    if not customer:
        return {"success": False, "message": "Customer not found"}

    company_id = request.session.get("company_id")
    from database import Plan
    plan_name = None
    if customer.plan_id:
        plan = db.query(Plan).filter(Plan.id == customer.plan_id, Plan.company_id == company_id).first()
        if plan:
            plan_name = plan.plan_name

    customer_data = {
        "customer_id": customer.customer_id,
        "service_type": customer.service_type,
        "username": customer.username,
        "customer_name": customer.customer_name,
        "customer_email": customer.customer_email,
        "customer_phone": customer.customer_phone,
        "alt_mobile": customer.alt_mobile,
        "gst_invoice_needed": customer.gst_invoice_needed,
        "customer_gst_no": customer.customer_gst_no,
        "id_proof": customer.id_proof,
        "id_proof_no": customer.id_proof_no,
        "installation_date": customer.installation_date,
        "address": customer.address,
        "locality": customer.locality,
        "state": customer.state,
        "city": customer.city,
        "pincode": customer.pincode,
        "plan_id": customer.plan_id,
        "plan_name": plan_name,
        "monthly_amount": float(customer.monthly_amount) if customer.monthly_amount is not None else None,
        "auto_renew": customer.auto_renew,
        "customer_type": customer.customer_type,
        "caf_no": customer.caf_no,
        "mac_address": customer.mac_address,
        "ip_address": customer.ip_address,
        "vendor": customer.vendor,
        "modem_no": customer.modem_no,
        "start_date": customer.start_date,
        "period": customer.period,
        "end_date": customer.end_date,
        "bill_amount": float(customer.bill_amount) if customer.bill_amount is not None else None,
        "cgst_tax": float(customer.cgst_tax) if customer.cgst_tax is not None else None,
        "sgst_tax": float(customer.sgst_tax) if customer.sgst_tax is not None else None,
        "igst_tax": float(customer.igst_tax) if customer.igst_tax is not None else None,
        "total_bill_amount": float(customer.total_bill_amount) if customer.total_bill_amount is not None else None,
        "security_deposit": float(customer.security_deposit) if customer.security_deposit is not None else None,
        "installation_charges": float(customer.installation_charges) if customer.installation_charges is not None else None,
        "received_amount": float(customer.received_amount) if customer.received_amount is not None else None,
        "router_charges": float(customer.router_charges) if customer.router_charges is not None else None,
        "discount_credit": float(customer.discount_credit) if customer.discount_credit is not None else 0.0,
        "payment_mode": customer.payment_mode,
        "transaction_id": customer.transaction_id,
        "payment_notes": customer.payment_notes,
        "status": customer.status,
    }
    try:
        customer_data["previous_due"] = calculate_total_due(customer, db)
    except Exception:
        customer_data["previous_due"] = 0.0
    return {"success": True, "customer": customer_data}


@app.get("/api/customers/{customer_id}/transactions")
async def api_customer_transactions(customer_id: str, request: Request, db: Session = Depends(get_db)):
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    customer = _get_scoped_customer(customer_id, request, db)
    if not customer:
        return {"success": False, "message": "Customer not found"}

    company_id = request.session.get("company_id")
    from database import Payment, Invoice, Transaction, Admin, Employee

    def _parse_date(v):
        if not v:
            return datetime.min
        if isinstance(v, datetime):
            return v
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y",
                    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
            try:
                return datetime.strptime(str(v), fmt)
            except Exception:
                continue
        return datetime.min

    try:
        admin_map = {a.admin_id: (a.admin_name or a.admin_id) for a in db.query(Admin).filter(Admin.company_id == company_id).all()}
        employee_map = {e.employee_code: (e.employee_name or e.employee_code) for e in db.query(Employee).filter(Employee.company_id == company_id).all()}

        invoices = db.query(Invoice).filter(
            Invoice.customer_id == customer_id,
            Invoice.company_id == company_id,
        ).order_by(Invoice.issue_date.asc(), Invoice.id.asc()).all()
        payments = db.query(Payment).filter(
            Payment.customer_id == customer_id,
            Payment.company_id == company_id,
        ).order_by(Payment.paid_at.asc(), Payment.id.asc()).all()

        invoice_txn_remarks = {}
        for t in db.query(Transaction).filter(
            Transaction.customer_id == customer_id,
            Transaction.company_id == company_id,
            Transaction.invoice_id.isnot(None),
        ).all():
            if t.invoice_id:
                invoice_txn_remarks[t.invoice_id] = t.remarks or "Invoice for service period"

        all_events = []
        for inv in invoices:
            created_at = inv.created_at or _parse_date(inv.issue_date)
            all_events.append({"type": "invoice", "date": created_at, "id": inv.id, "data": inv,
                               "remarks": invoice_txn_remarks.get(inv.id, "Invoice for service period")})
        for pay in payments:
            created_at = pay.created_at or _parse_date(pay.paid_at)
            all_events.append({"type": "payment", "date": created_at, "id": pay.id, "data": pay,
                               "remarks": pay.remarks or f"Payment via {pay.payment_mode or 'CASH'}"})
        all_events.sort(key=lambda x: (x["date"], x["id"]))

        running_balance = 0.0
        transactions = []
        for ev in all_events:
            if ev["type"] == "invoice":
                inv = ev["data"]
                amount = float(inv.total_amount) if inv.total_amount else 0.0
                running_balance += amount
                performed_by = "Admin"
                if getattr(inv, "created_by", None):
                    performed_by = admin_map.get(inv.created_by) or employee_map.get(inv.created_by) or inv.created_by
                transactions.append({
                    "type": "invoice",
                    "transaction_no": inv.invoice_no,
                    "paid_at": ev["date"].strftime("%d-%m-%Y") if ev["date"] != datetime.min else "",
                    "amount": amount,
                    "discount": 0.0,
                    "payment_mode": "",
                    "remarks": ev["remarks"],
                    "payment_remarks": "",
                    "employee_id": "ADMIN001",
                    "performed_by": performed_by,
                    "balance_after": round(running_balance),
                    "invoice_id": inv.id,
                    "has_invoice": True,
                    "download_url": f"/api/invoices/{inv.id}/download",
                    "email_url": f"/api/invoices/{inv.id}/send-email",
                })
            else:
                pay = ev["data"]
                amount = float(pay.amount) if pay.amount else 0.0
                discount = float(pay.discount) if pay.discount else 0.0
                total_payment = amount + discount
                running_balance -= total_payment
                remarks = ev["remarks"] + (f" (Discount: ₹{int(discount)})" if discount > 0 else "")
                emp_id = pay.employee_id or "ADMIN001"
                performed_by = employee_map.get(emp_id) or admin_map.get(emp_id) or emp_id
                transactions.append({
                    "type": "payment",
                    "transaction_no": pay.transaction_no,
                    "paid_at": ev["date"].strftime("%d-%m-%Y %H:%M") if ev["date"] != datetime.min else "",
                    "amount": amount,
                    "discount": discount,
                    "payment_mode": pay.payment_mode,
                    "remarks": remarks,
                    "payment_remarks": pay.remarks or "",
                    "employee_id": pay.employee_id or "ADMIN001",
                    "performed_by": performed_by,
                    "balance_after": round(running_balance),
                    "has_invoice": False,
                    "payment_id": pay.id,
                    "download_url": f"/api/payments/{pay.id}/receipt",
                    "email_url": f"/api/payments/{pay.id}/send-email",
                })
        transactions.reverse()

        return {
            "success": True,
            "transactions": transactions,
            "customer": {
                "customer_id": customer.customer_id,
                "customer_name": customer.customer_name,
                "customer_email": customer.customer_email,
                "customer_phone": customer.customer_phone,
                "address": customer.address,
                "current_balance": float(customer.total_bill_amount) if customer.total_bill_amount else 0.0,
            },
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/customers/update")
async def api_customers_update(request: Request, db: Session = Depends(get_db)):
    _p = require_permission_api(request, db, "feat.global_edit")
    if _p:
        return _p
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    def parse_float(v):
        if v is None or v == "":
            return None
        try:
            return float(str(v).replace("₹", "").replace(",", "").strip())
        except Exception:
            return None

    def parse_int(v):
        if v is None or v == "":
            return None
        try:
            return int(v)
        except Exception:
            return None

    try:
        data = await request.json()
        if not data.get("customer_id"):
            return {"success": False, "message": "Customer ID is required"}

        customer = _get_scoped_customer(data["customer_id"], request, db)
        if not customer:
            return {"success": False, "message": "Customer not found"}

        customer.service_type = data.get("service_type", customer.service_type)
        customer.customer_name = data.get("name", customer.customer_name)
        customer.username = data.get("username", customer.username)
        customer.customer_email = data.get("email", customer.customer_email)
        customer.customer_phone = data.get("mobile", customer.customer_phone)
        customer.alt_mobile = data.get("alt_mobile")
        customer.gst_invoice_needed = data.get("gst_invoice_needed", "NO")
        customer.customer_gst_no = data.get("customer_gst_no")
        customer.id_proof = data.get("id_proof")
        customer.id_proof_no = data.get("id_proof_no")
        customer.installation_date = data.get("installation_date")
        customer.address = data.get("address")
        customer.locality = data.get("locality")
        customer.state = data.get("state")
        customer.city = data.get("city")
        customer.pincode = data.get("pincode")
        customer.plan_id = parse_int(data.get("plan"))
        customer.monthly_amount = parse_float(data.get("monthly_amount"))
        customer.auto_renew = data.get("auto_renew", "Yes")
        customer.customer_type = data.get("customer_type", "Postpaid")
        customer.caf_no = data.get("caf_no")
        customer.mac_address = data.get("mac_address")
        customer.ip_address = data.get("ip_address")
        customer.vendor = data.get("vendor")
        customer.modem_no = data.get("modem_no")
        customer.start_date = data.get("start_date")
        customer.period = parse_int(data.get("period"))
        customer.end_date = data.get("end_date")

        if "bill_amount" in data:
            customer.bill_amount = parse_float(data.get("bill_amount"))
        if "cgst_tax" in data:
            customer.cgst_tax = parse_float(data.get("cgst_tax"))
        if "sgst_tax" in data:
            customer.sgst_tax = parse_float(data.get("sgst_tax"))
        if "igst_tax" in data:
            customer.igst_tax = parse_float(data.get("igst_tax"))
        if "total_bill_amount" in data:
            customer.total_bill_amount = parse_float(data.get("total_bill_amount"))

        customer.security_deposit = parse_float(data.get("security_deposit"))
        customer.installation_charges = parse_float(data.get("installation_charges"))
        customer.received_amount = parse_float(data.get("received_amount"))
        customer.router_charges = parse_float(data.get("router_charges"))

        if "discount_credit" in data:
            dv = parse_float(data.get("discount_credit"))
            if dv is not None:
                customer.discount_credit = dv

        customer.payment_mode = data.get("payment_mode")
        customer.transaction_id = data.get("transaction_id")
        customer.payment_notes = data.get("payment_notes")

        db.commit()
        return {"success": True, "message": "Customer updated successfully"}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}


@app.patch("/api/customers/{customer_id}/status")
async def api_customer_status(customer_id: str, request: Request, db: Session = Depends(get_db)):
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    customer = _get_scoped_customer(customer_id, request, db)
    if not customer:
        return {"success": False, "message": "Customer not found"}

    try:
        data = await request.json()
        new_status = (data.get("status") or "").strip()
        if new_status not in ("Active", "Deactive", "Pending", "Deleted"):
            return {"success": False, "message": "Invalid status"}
        customer.status = new_status
        db.commit()
        return {"success": True, "message": f"Customer status set to {new_status}"}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}


@app.patch("/api/customers/{customer_id}/auto-renew")
async def api_customer_auto_renew(customer_id: str, request: Request, db: Session = Depends(get_db)):
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    customer = _get_scoped_customer(customer_id, request, db)
    if not customer:
        return {"success": False, "message": "Customer not found"}

    try:
        data = await request.json()
        val = (data.get("auto_renew") or "").strip()
        if val not in ("Yes", "No"):
            return {"success": False, "message": "auto_renew must be 'Yes' or 'No'"}
        customer.auto_renew = val
        db.commit()
        return {"success": True, "message": f"Auto renew set to {val}"}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}


@app.patch("/api/customers/{customer_id}/end-date")
async def api_customer_end_date(customer_id: str, request: Request, db: Session = Depends(get_db)):
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    customer = _get_scoped_customer(customer_id, request, db)
    if not customer:
        return {"success": False, "message": "Customer not found"}

    try:
        data = await request.json()
        end_date = data.get("end_date")
        if not end_date:
            return {"success": False, "message": "end_date is required"}
        customer.end_date = end_date
        db.commit()
        return {"success": True, "message": "End date updated"}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}


@app.post("/api/customers/{customer_id}/balance-adjustment")
async def api_customer_balance_adjustment(customer_id: str, request: Request, db: Session = Depends(get_db)):
    _p = require_permission_api(request, db, "feat.get_paid")
    if _p:
        return _p
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    customer = _get_scoped_customer(customer_id, request, db)
    if not customer:
        return {"success": False, "message": "Customer not found"}

    try:
        data = await request.json()
        adj = float(data.get("adjustment", 0) or 0)
        note = (data.get("note") or "").strip()
        customer.discount_credit = float(customer.discount_credit or 0) + adj
        if note:
            customer.payment_notes = note
        db.commit()
        return {"success": True, "message": "Balance adjusted", "discount_credit": customer.discount_credit}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}


@app.post("/api/customers/{customer_id}/addon-bill")
async def api_customer_addon_bill(customer_id: str, request: Request, db: Session = Depends(get_db)):
    _p = require_permission_api(request, db, "feat.invoice")
    if _p:
        return _p
    """Create an addon Invoice for the given customer."""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    customer = _get_scoped_customer(customer_id, request, db)
    if not customer:
        return {"success": False, "message": "Customer not found"}

    company_id = request.session.get("company_id")
    from database import Invoice, Transaction, Company

    try:
        try:
            data = await request.json()
        except Exception:
            form = await request.form()
            data = dict(form)

        amount = float(data.get("amount") or 0)
        description = (data.get("description") or data.get("remarks") or "Addon bill").strip()
        if amount <= 0:
            return {"success": False, "message": "Amount must be greater than zero"}

        company = db.query(Company).filter(Company.company_id == company_id).first()
        tax = compute_tax_breakdown(
            getattr(company, "state", None) or "",
            customer.state or "",
            amount,
        )

        invoice_no = generate_invoice_number(company_id, db)
        today = datetime.utcnow().strftime("%Y-%m-%d")

        inv = Invoice(
            company_id=company_id,
            customer_id=customer.customer_id,
            invoice_no=invoice_no,
            issue_date=today,
            due_date=today,
            base_amount=amount,
            cgst_tax=tax["cgst_tax"],
            sgst_tax=tax["sgst_tax"],
            igst_tax=tax["igst_tax"],
            total_amount=tax["total_amount"],
            status="generated",
            created_at=datetime.utcnow(),
        )
        db.add(inv)
        db.flush()

        txn = Transaction(
            company_id=company_id,
            customer_id=customer.customer_id,
            invoice_id=inv.id,
            transaction_no=invoice_no,
            amount=tax["total_amount"],
            status="pending",
            remarks=f"Addon: {description}",
            created_at=datetime.utcnow(),
        )
        db.add(txn)
        db.commit()
        return {"success": True, "message": "Addon bill created", "invoice_no": invoice_no,
                "total_amount": tax["total_amount"]}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}


@app.post("/api/customers/{customer_id}/renew")
async def api_customer_renew(customer_id: str, request: Request, db: Session = Depends(get_db)):
    _p = require_permission_api(request, db, "feat.renew_button")
    if _p:
        return _p
    """Renew a customer's subscription. Uses services/billing.renew_customer_core if available."""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    customer = _get_scoped_customer(customer_id, request, db)
    if not customer:
        return {"success": False, "message": "Customer not found"}

    company_id = request.session.get("company_id")
    try:
        try:
            data = await request.json()
        except Exception:
            form = await request.form()
            data = dict(form)

        with_invoice = bool(data.get("with_invoice", True))
        period_months = int(data.get("period_months") or data.get("period") or 1)

        start_str = data.get("start_date")
        if start_str:
            try:
                start_date = datetime.strptime(start_str, "%Y-%m-%d")
            except Exception:
                start_date = datetime.utcnow()
        else:
            start_date = datetime.utcnow()

        try:
            from services.billing import renew_customer_core
            result = renew_customer_core(
                company_id=company_id,
                customer_id=customer.customer_id,
                start_date=start_date,
                period_months=period_months,
                with_invoice=with_invoice,
                db=db,
                source="manual",
            )
            return {"success": True, "message": "Customer renewed", **(result or {})}
        except Exception:
            end_date = (start_date + timedelta(days=30 * max(1, period_months)))
            customer.start_date = start_date.strftime("%Y-%m-%d")
            customer.end_date = end_date.strftime("%Y-%m-%d")
            customer.period = period_months
            if customer.status == "Deactive":
                customer.status = "Active"
            db.commit()
            return {"success": True, "message": "Customer renewed (fallback)",
                    "start_date": customer.start_date, "end_date": customer.end_date}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}


@app.post("/api/customers/{customer_id}/renew/revert")
async def api_customer_renew_revert(customer_id: str, request: Request, db: Session = Depends(get_db)):
    _p = require_permission_api(request, db, "feat.renew_button")
    if _p:
        return _p
    """Revert the most recent renewal by deleting the latest invoice for the customer."""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    customer = _get_scoped_customer(customer_id, request, db)
    if not customer:
        return {"success": False, "message": "Customer not found"}

    company_id = request.session.get("company_id")
    from database import Invoice, Transaction
    try:
        latest_invoice = db.query(Invoice).filter(
            Invoice.company_id == company_id,
            Invoice.customer_id == customer.customer_id,
        ).order_by(Invoice.id.desc()).first()
        if not latest_invoice:
            return {"success": False, "message": "No invoice to revert"}

        db.query(Transaction).filter(
            Transaction.invoice_id == latest_invoice.id,
            Transaction.company_id == company_id,
        ).delete(synchronize_session=False)
        db.delete(latest_invoice)
        db.commit()
        return {"success": True, "message": "Latest renewal reverted"}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}


@app.post("/api/customers/{customer_id}/send-payment-link")
async def api_customer_send_payment_link(customer_id: str, request: Request, db: Session = Depends(get_db)):
    _p = require_permission_api(request, db, "feat.invoice")
    if _p:
        return _p
    """Record intent to send a payment link. Actual email/SMS delivery is handled by the admin portal."""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    customer = _get_scoped_customer(customer_id, request, db)
    if not customer:
        return {"success": False, "message": "Customer not found"}
    return {"success": True, "message": f"Payment link queued for {customer.customer_name}"}


@app.get("/api/customers/{customer_id}/download-caf")
async def api_customer_download_caf(customer_id: str, request: Request, db: Session = Depends(get_db)):
    auth_check = require_auth(request)
    if auth_check:
        raise HTTPException(status_code=401, detail="Unauthorized")

    customer = _get_scoped_customer(customer_id, request, db)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    caf_blob = getattr(customer, "caf_pdf", None)
    if not caf_blob:
        raise HTTPException(status_code=404, detail="CAF not available for this customer")
    return Response(
        content=caf_blob,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=CAF_{customer.customer_id}.pdf"},
    )


# ============================================================================
# /api/transactions/*
# ============================================================================

@app.get("/api/transactions/list")
async def api_transactions_list(
    request: Request,
    db: Session = Depends(get_db),
    status: str = None,
    customer_id: str = None,
    payment_mode: str = None,
    start_date: str = None,
    end_date: str = None,
):
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    company_id = request.session.get("company_id")
    from database import Transaction

    try:
        scoped_ids_q = scope_customers_to_employee(
            db.query(Customer.customer_id).filter(Customer.company_id == company_id),
            request, db,
        )
        scoped_ids = {row[0] for row in scoped_ids_q.all()}

        query = db.query(Transaction, Customer).join(
            Customer, Transaction.customer_id == Customer.customer_id,
        ).filter(Transaction.company_id == company_id)

        if status:
            query = query.filter(Transaction.status == status)
        if customer_id:
            query = query.filter(Transaction.customer_id == customer_id)
        if payment_mode:
            query = query.filter(Transaction.payment_mode == payment_mode)
        if start_date:
            query = query.filter(Transaction.payment_date >= start_date)
        if end_date:
            query = query.filter(Transaction.payment_date <= end_date)

        rows = query.order_by(Transaction.created_at.desc()).limit(500).all()

        transactions = []
        for txn, cust in rows:
            if request.session.get("user_type") == "employee" and cust.customer_id not in scoped_ids:
                continue
            transactions.append({
                "id": txn.id,
                "transaction_no": txn.transaction_no,
                "customer_id": txn.customer_id,
                "customer_name": cust.customer_name,
                "amount": float(txn.amount) if txn.amount is not None else 0.0,
                "payment_mode": txn.payment_mode,
                "payment_date": txn.payment_date,
                "status": txn.status,
                "remarks": txn.remarks,
            })
        return {"success": True, "transactions": transactions}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ============================================================================
# /api/complaints/*
# ============================================================================

@app.get("/api/complaints/list")
async def api_complaints_list(
    request: Request,
    db: Session = Depends(get_db),
    status: str = None,
    priority: str = None,
    complaint_type: str = None,
):
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    company_id = request.session.get("company_id")
    from database import Complaint

    try:
        scoped_ids_q = scope_customers_to_employee(
            db.query(Customer.customer_id).filter(Customer.company_id == company_id),
            request, db,
        )
        scoped_ids = {row[0] for row in scoped_ids_q.all()}

        q = db.query(Complaint, Customer).outerjoin(
            Customer, Complaint.customer_id == Customer.customer_id,
        ).filter(Complaint.company_id == company_id)

        if status:
            q = q.filter(Complaint.status == status)
        if priority:
            q = q.filter(Complaint.priority == priority)
        if complaint_type:
            q = q.filter(Complaint.complaint_type == complaint_type)

        rows = q.order_by(Complaint.created_at.desc()).limit(500).all()
        complaints = []
        for comp, cust in rows:
            if request.session.get("user_type") == "employee":
                if comp.customer_id and comp.customer_id not in scoped_ids:
                    continue
            complaints.append({
                "id": comp.id,
                "ticket_no": comp.ticket_no,
                "customer_id": comp.customer_id,
                "customer_name": cust.customer_name if cust else "",
                "complaint_type": comp.complaint_type,
                "priority": comp.priority,
                "subject": comp.subject,
                "description": comp.description,
                "status": comp.status,
                "created_at": comp.created_at.isoformat() if comp.created_at else None,
                "resolved_at": comp.resolved_at.isoformat() if comp.resolved_at else None,
            })
        return {"success": True, "complaints": complaints}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/complaints/{complaint_id}/update-status")
async def api_complaint_update_status(complaint_id: int, request: Request, db: Session = Depends(get_db)):
    _p = require_permission_api(request, db, "feat.complaints")
    if _p:
        return _p
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    company_id = request.session.get("company_id")
    from database import Complaint

    try:
        try:
            data = await request.json()
        except Exception:
            form = await request.form()
            data = dict(form)

        new_status = (data.get("status") or "").strip()
        if new_status not in ("Open", "In Progress", "Resolved", "Closed"):
            return {"success": False, "message": "Invalid status"}

        complaint = db.query(Complaint).filter(
            Complaint.id == complaint_id,
            Complaint.company_id == company_id,
        ).first()
        if not complaint:
            return {"success": False, "message": "Complaint not found"}

        if request.session.get("user_type") == "employee" and complaint.customer_id:
            customer = db.query(Customer).filter(
                Customer.customer_id == complaint.customer_id,
                Customer.company_id == company_id,
            ).first()
            if not customer or not is_customer_in_employee_scope(customer, request, db):
                return {"success": False, "message": "Out of scope"}

        complaint.status = new_status
        if new_status in ("Resolved", "Closed"):
            complaint.resolved_at = datetime.utcnow()
            complaint.resolved_by = request.session.get("user_id") or "employee"
        db.commit()
        return {"success": True, "message": "Status updated"}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}


# ============================================================================
# /api/payments/*
# ============================================================================

@app.get("/api/payments/transaction-no")
async def api_payments_transaction_no_preview(
    request: Request, payment_mode: str = "Cash", db: Session = Depends(get_db)
):
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    try:
        txn_no = generate_transaction_no(payment_mode, db)
        return {"success": True, "transaction_no": txn_no}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/payments/create")
async def api_payments_create(request: Request, db: Session = Depends(get_db)):
    _p = require_permission_api(request, db, "feat.get_paid")
    if _p:
        return _p
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}

    company_id = request.session.get("company_id")
    from database import Payment, ReceivedTracker

    try:
        try:
            data = await request.json()
        except Exception:
            form = await request.form()
            data = dict(form)

        customer_id = (data.get("customer_id") or "").strip()
        if not customer_id:
            return {"success": False, "message": "Customer ID is required"}

        customer = _get_scoped_customer(customer_id, request, db)
        if not customer:
            return {"success": False, "message": "Customer not found"}

        def _f(v):
            try:
                return float(str(v).replace("₹", "").replace(",", "").strip()) if v not in (None, "") else 0.0
            except Exception:
                return 0.0

        amount = _f(data.get("amount"))
        discount = _f(data.get("discount"))
        if amount <= 0 and discount <= 0:
            return {"success": False, "message": "Amount or discount must be greater than zero"}

        payment_mode = (data.get("payment_mode") or "Cash").strip()
        provided_txn = (data.get("transaction_no") or "").strip()
        transaction_no = provided_txn or generate_transaction_no(payment_mode, db)
        paid_at = data.get("paid_at") or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        pay = Payment(
            company_id=company_id,
            customer_id=customer.customer_id,
            transaction_no=transaction_no,
            amount=amount,
            discount=discount,
            payment_mode=payment_mode,
            paid_at=paid_at,
            remarks=data.get("remarks") or "",
            employee_id=request.session.get("user_id"),
            created_at=datetime.utcnow(),
        )
        db.add(pay)

        tracker = db.query(ReceivedTracker).filter(
            ReceivedTracker.company_id == company_id,
            ReceivedTracker.customer_id == customer.customer_id,
        ).first()
        if not tracker:
            tracker = ReceivedTracker(
                company_id=company_id,
                customer_id=customer.customer_id,
                received_since_reset=0,
            )
            db.add(tracker)
        tracker.received_since_reset = float(tracker.received_since_reset or 0) + amount

        db.commit()
        db.refresh(pay)
        # _S40zM_ Auto-email receipt (best-effort; cross-service HTTP to admin helper).
        try:
            import httpx as _httpx_R
            _httpx_R.post(
                f"{os.environ.get('ISP_ADMIN_URL', os.environ.get('ISP_ADMIN_URL', 'http://127.0.0.1:8001'))}/api/internal/payments/{pay.id}/send-receipt-email",
                params={"company_id": company_id}, timeout=8,
            )
        except Exception as _eR:
            print(f"[employee receipt-email auto] payment#{pay.id}: {_eR}")
        return {"success": True, "message": "Payment recorded", "payment_id": pay.id,
                "transaction_no": transaction_no}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}


@app.post("/api/payments/{payment_id}/send-email")
async def api_payments_send_email(payment_id: int, request: Request, db: Session = Depends(get_db)):
    """Queue a receipt email send. Actual SMTP send is handled by the admin portal."""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    from database import Payment
    company_id = request.session.get("company_id")
    payment = db.query(Payment).filter(
        Payment.id == payment_id, Payment.company_id == company_id
    ).first()
    if not payment:
        return {"success": False, "message": "Payment not found"}
    return {"success": True, "message": "Receipt email queued"}


@app.get("/api/payments/{payment_id}/receipt")
async def api_payments_receipt(payment_id: int, request: Request, db: Session = Depends(get_db)):
    """Minimal text receipt. A full PDF receipt is generated by the admin portal."""
    auth_check = require_auth(request)
    if auth_check:
        raise HTTPException(status_code=401, detail="Unauthorized")

    company_id = request.session.get("company_id")
    from database import Payment, Company
    payment = db.query(Payment).filter(
        Payment.id == payment_id, Payment.company_id == company_id
    ).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    customer = db.query(Customer).filter(
        Customer.customer_id == payment.customer_id,
        Customer.company_id == company_id,
    ).first()

    if customer and not is_customer_in_employee_scope(customer, request, db):
        raise HTTPException(status_code=403, detail="Out of scope")

    company = db.query(Company).filter(Company.company_id == company_id).first()
    lines = [
        f"RECEIPT #{payment.transaction_no}",
        f"Company: {company.company_name if company else ''}",
        f"Customer: {customer.customer_name if customer else payment.customer_id}",
        f"Amount: {float(payment.amount or 0):.2f}",
        f"Discount: {float(payment.discount or 0):.2f}",
        f"Payment Mode: {payment.payment_mode or ''}",
        f"Paid At: {payment.paid_at or ''}",
        f"Remarks: {payment.remarks or ''}",
    ]
    return Response(content="\n".join(lines), media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8003)

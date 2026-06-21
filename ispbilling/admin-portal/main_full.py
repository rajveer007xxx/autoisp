# __PHASE4_INACTIVE__  main_full.py is a stale 'all-in-one' snapshot kept
# only for reference. Loading it would re-import the live app twice and
# bypass db_compat. Phase-4 hard-disables it.
import os
if not os.environ.get('ISP_ALLOW_MAIN_FULL'):
    raise RuntimeError(
        'main_full.py is disabled in Phase-4. Use the live main.py instead.'
    )

from fastapi import FastAPI, Request, Depends, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func, text, select
from sqlalchemy.exc import IntegrityError
import uvicorn
import random
import string
import os
from datetime import datetime

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
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA table_info(customers)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if 'customer_type' not in columns:
        cursor.execute("ALTER TABLE customers ADD COLUMN customer_type TEXT DEFAULT 'Postpaid'")
        cursor.execute("UPDATE customers SET customer_type = 'Postpaid' WHERE customer_type IS NULL")
        conn.commit()
    
    conn.close()
    
    print("✓ Application startup complete")

def require_auth(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse(url="/login", status_code=303)
    return None

def require_admin(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse(url="/login", status_code=303)
    if request.session.get("user_type") != "admin":
        return RedirectResponse(url="/login", status_code=303)
    return None

def require_superadmin(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse(url="/login", status_code=303)
    if request.session.get("user_type") != "superadmin":
        return RedirectResponse(url="/login", status_code=303)
    return None

def require_employee(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse(url="/login", status_code=303)
    if request.session.get("user_type") != "employee":
        return RedirectResponse(url="/login", status_code=303)
    
    from database import Employee
    db = next(get_db())
    try:
        employee_id = request.session.get("employee_id")
        company_id = request.session.get("company_id")
        
        if employee_id and company_id:
            employee = db.query(Employee).filter(
                Employee.id == employee_id,
                Employee.company_id == company_id,
                Employee.is_deleted == False
            ).first()
            
            if not employee or employee.status != 'Active':
                # Employee is deactivated or deleted, clear session and redirect to login
                request.session.clear()
                return RedirectResponse(url="/login", status_code=303)
    finally:
        db.close()
    
    return None

def require_not_employee(request: Request):
    """Block employees from accessing destructive operations"""
    if request.session.get("user_type") == "employee":
        raise HTTPException(status_code=403, detail="Employees cannot perform this action")
    return None

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

@app.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

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

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, "dashboard")
    return templates.TemplateResponse("admin_dashboard.html", context)

@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request, db: Session = Depends(get_db)):
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, "users")
    company_id = request.session.get("company_id")
    
    from database import Customer, Payment, Plan, Invoice, ReceivedTracker
    from datetime import datetime, timedelta
    
    customers = db.query(Customer).filter(
        Customer.company_id == company_id,
        Customer.status != "Deleted"
    ).all()
    
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
    return templates.TemplateResponse("admin_users.html", context)

@app.get("/admin/add-cable", response_class=HTMLResponse)
async def add_cable(request: Request, db: Session = Depends(get_db)):
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, "users")
    return templates.TemplateResponse("add_cable.html", context)

@app.get("/admin/add-broadband", response_class=HTMLResponse)
async def add_broadband(request: Request, db: Session = Depends(get_db)):
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, "users")
    return templates.TemplateResponse("add_broadband.html", context)

@app.get("/admin/profile", response_class=HTMLResponse)
async def admin_profile(request: Request, db: Session = Depends(get_db)):
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, "profile")
    return templates.TemplateResponse("admin_profile.html", context)

@app.get("/admin/plans", response_class=HTMLResponse)
async def admin_plans(request: Request, db: Session = Depends(get_db)):
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, "plans")
    company_id = request.session.get("company_id", "N/A")
    
    from database import Plan
    plans = db.query(Plan).filter(Plan.company_id == company_id).all()
    context["plans"] = plans
    
    return templates.TemplateResponse("admin_plans.html", context)

@app.get("/admin/employees", response_class=HTMLResponse)
async def admin_employees(request: Request, db: Session = Depends(get_db)):
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, "employees")
    return templates.TemplateResponse("admin_employees.html", context)

@app.get("/admin/add-employee", response_class=HTMLResponse)
async def admin_add_employee(request: Request, db: Session = Depends(get_db)):
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, "employees")
    return templates.TemplateResponse("admin_add_employee.html", context)

@app.get("/admin/edit-employee", response_class=HTMLResponse)
async def admin_edit_employee(request: Request, db: Session = Depends(get_db)):
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, "employees")
    return templates.TemplateResponse("admin_edit_employee.html", context)

@app.get("/admin/track-employee", response_class=HTMLResponse)
async def admin_track_employee(request: Request, db: Session = Depends(get_db)):
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, "employees")
    return templates.TemplateResponse("admin_track_employees_google.html", context)

@app.post("/api/plans")
async def create_plan(request: Request, db: Session = Depends(get_db)):
    """Create a new plan"""
    auth_check = require_auth(request)
    if auth_check:
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    
    company_id = request.session.get("company_id")
    if not company_id:
        return JSONResponse({"success": False, "message": "Company ID not found"}, status_code=400)
    
    from database import Plan
    
    try:
        data = await request.json()
        
        base_amount = float(data.get("base_amount", 0))
        cgst_tax = float(data.get("cgst_tax", 0))
        sgst_tax = float(data.get("sgst_tax", 0))
        igst_tax = float(data.get("igst_tax", 0))
        
        if cgst_tax > 0 or sgst_tax > 0:
            cgst_amount = (base_amount * cgst_tax) / 100
            sgst_amount = (base_amount * sgst_tax) / 100
            after_tax_amount = round(base_amount + cgst_amount + sgst_amount)
        else:
            igst_amount = (base_amount * igst_tax) / 100
            after_tax_amount = round(base_amount + igst_amount)
        
        validity_str = data.get("validity", "30")
        if "month" in validity_str.lower():
            validity = 30
        else:
            validity = int(validity_str)
        
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
            description=data.get("description", "")
        )
        
        db.add(new_plan)
        db.commit()
        db.refresh(new_plan)
        
        return JSONResponse({"success": True, "message": "Plan saved successfully", "plan_id": new_plan.id})
    except Exception as e:
        db.rollback()
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)

@app.get("/api/plans/list")
async def list_plans(request: Request, service: str = None, db: Session = Depends(get_db)):
    """Get list of plans filtered by service type"""
    auth_check = require_auth(request)
    if auth_check:
        return {"error": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Plan
    
    query = db.query(Plan).filter(Plan.company_id == company_id)
    if service:
        query = query.filter(Plan.service == service)
    
    plans = query.all()
    
    return {
        "plans": [
            {
                "id": plan.id,
                "plan_name": plan.plan_name,
                "speed": plan.speed,
                "service": plan.service,
                "validity": plan.validity,
                "base_amount": plan.base_amount,
                "cgst_tax": plan.cgst_tax,
                "sgst_tax": plan.sgst_tax,
                "igst_tax": plan.igst_tax,
                "after_tax_amount": plan.after_tax_amount,
                "description": plan.description
            }
            for plan in plans
        ]
    }

@app.get("/api/plans/{plan_id}")
async def get_plan(plan_id: int, request: Request, db: Session = Depends(get_db)):
    """Get a single plan by ID"""
    auth_check = require_auth(request)
    if auth_check:
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    
    company_id = request.session.get("company_id")
    if not company_id:
        return JSONResponse({"success": False, "message": "Company ID not found"}, status_code=400)
    
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
        "base_amount": round(plan.base_amount),
        "cgst_tax": plan.cgst_tax,
        "sgst_tax": plan.sgst_tax,
        "igst_tax": plan.igst_tax,
        "after_tax_amount": round(plan.after_tax_amount),
        "description": plan.description
    }

@app.put("/api/plans/{plan_id}")
async def update_plan(plan_id: int, request: Request, db: Session = Depends(get_db)):
    """Update an existing plan (admin only)"""
    auth_check = require_admin(request)
    if auth_check:
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    
    company_id = request.session.get("company_id")
    if not company_id:
        return JSONResponse({"success": False, "message": "Company ID not found"}, status_code=400)
    
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
        
        if cgst_tax > 0 or sgst_tax > 0:
            cgst_amount = (base_amount * cgst_tax) / 100
            sgst_amount = (base_amount * sgst_tax) / 100
            after_tax_amount = round(base_amount + cgst_amount + sgst_amount)
        else:
            igst_amount = (base_amount * igst_tax) / 100
            after_tax_amount = round(base_amount + igst_amount)
        
        validity_str = data.get("validity", str(plan.validity))
        if "month" in validity_str.lower():
            validity = 30
        else:
            validity = int(validity_str)
        
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
async def delete_plan(plan_id: int, request: Request, db: Session = Depends(get_db)):
    """Delete a plan (admin only)"""
    auth_check = require_admin(request)
    if auth_check:
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    
    company_id = request.session.get("company_id")
    if not company_id:
        return JSONResponse({"success": False, "message": "Company ID not found"}, status_code=400)
    
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

@app.get("/api/profile/get")
async def get_profile(request: Request, db: Session = Depends(get_db)):
    """Get current admin profile data"""
    auth_check = require_auth(request)
    if auth_check:
        return {"error": "Unauthorized"}
    
    user_id = request.session.get("user_id")
    company_id = request.session.get("company_id")
    
    from database import Admin, Company
    
    # Get admin data
    admin = db.query(Admin).filter(Admin.admin_id == user_id).first()
    if not admin:
        return {"error": "Admin not found"}
    
    # Get company data
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        return {"error": "Company not found"}
    
    return {
        "admin": {
            "admin_id": admin.admin_id,
            "admin_name": admin.admin_name,
            "admin_email": admin.admin_email,
            "admin_mobile": admin.admin_mobile,
            "profile_image_path": admin.profile_image_path
        },
        "company": {
            "company_id": company.company_id,
            "company_name": company.company_name,
            "company_email": company.company_email,
            "company_phone": company.company_phone,
            "company_address": company.company_address,
            "country": company.country,
            "state": company.state,
            "city": company.city,
            "pincode": company.pincode,
            "gst_number": company.gst_number,
            "bank_name": company.bank_name,
            "account_number": company.account_number,
            "branch_code": company.branch_code,
            "branch_location": company.branch_location,
            "branch_ifsc": company.branch_ifsc,
            "upi_id": company.upi_id,
            "logo_path": company.logo_path,
            "declaration": company.declaration or "",
            "terms_conditions": company.terms_conditions or ""
        }
    }

@app.post("/api/profile/update")
async def update_profile(request: Request, db: Session = Depends(get_db)):
    """Update admin profile data"""
    auth_check = require_auth(request)
    if auth_check:
        return {"error": "Unauthorized"}
    
    user_id = request.session.get("user_id")
    company_id = request.session.get("company_id")
    
    from database import Admin, Company
    import bcrypt
    
    form_data = await request.form()
    
    # Update admin data
    admin = db.query(Admin).filter(Admin.admin_id == user_id).first()
    if admin:
        admin.admin_name = form_data.get("name", admin.admin_name)
        admin.admin_email = form_data.get("email", admin.admin_email)
        admin.admin_mobile = form_data.get("mobile", admin.admin_mobile)
        
        old_password = form_data.get("old_password")
        new_password = form_data.get("password")
        if old_password and new_password:
            # Verify old password
            if bcrypt.checkpw(old_password.encode('utf-8'), admin.password_hash.encode('utf-8')):
                # Hash new password
                hashed = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
                admin.password_hash = hashed.decode('utf-8')
            else:
                return {"error": "Old password is incorrect"}
    
    # Update company data
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if company:
        company.company_name = form_data.get("company_name", company.company_name)
        company.company_email = form_data.get("email", company.company_email)
        company.company_phone = form_data.get("mobile", company.company_phone)
        company.company_address = form_data.get("company_address", company.company_address)
        company.country = form_data.get("country", company.country)
        company.state = form_data.get("state", company.state)
        company.city = form_data.get("city", company.city)
        company.pincode = form_data.get("pincode", company.pincode)
        company.gst_number = form_data.get("gst_number", company.gst_number)
        company.bank_name = form_data.get("bank_name", company.bank_name)
        company.account_number = form_data.get("account_no", company.account_number)
        company.branch_code = form_data.get("branch_code", company.branch_code)
        company.branch_location = form_data.get("branch_location", company.branch_location)
        company.branch_ifsc = form_data.get("ifsc", company.branch_ifsc)
        company.upi_id = form_data.get("upi_id", company.upi_id)
        company.declaration = form_data.get("declaration", company.declaration)
        company.terms_conditions = form_data.get("terms_conditions", company.terms_conditions)
        
        import os
        import uuid
        from pathlib import Path
        
        qr_code = form_data.get("qr_code")
        if qr_code and hasattr(qr_code, 'filename') and qr_code.filename:
            allowed_extensions = {'.jpg', '.jpeg', '.png', '.webp'}
            file_ext = os.path.splitext(qr_code.filename)[1].lower()
            if file_ext in allowed_extensions:
                upload_dir = Path(f"static/uploads/{company_id}/company")
                upload_dir.mkdir(parents=True, exist_ok=True)
                unique_filename = f"qr-{uuid.uuid4()}{file_ext}"
                file_path = upload_dir / unique_filename
                contents = await qr_code.read()
                if len(contents) <= 2 * 1024 * 1024:  # 2MB limit
                    with open(file_path, "wb") as f:
                        f.write(contents)
                    company.bank_qr_code = str(file_path)
        
        company_logo = form_data.get("company_logo")
        if company_logo and hasattr(company_logo, 'filename') and company_logo.filename:
            allowed_extensions = {'.jpg', '.jpeg', '.png', '.webp'}
            file_ext = os.path.splitext(company_logo.filename)[1].lower()
            if file_ext in allowed_extensions:
                upload_dir = Path(f"static/uploads/{company_id}/company")
                upload_dir.mkdir(parents=True, exist_ok=True)
                unique_filename = f"logo-{uuid.uuid4()}{file_ext}"
                file_path = upload_dir / unique_filename
                contents = await company_logo.read()
                if len(contents) <= 2 * 1024 * 1024:  # 2MB limit
                    with open(file_path, "wb") as f:
                        f.write(contents)
                    company.logo_path = f"/static/uploads/{company_id}/company/{unique_filename}"
        
        profile_image = form_data.get("profile_image")
        if profile_image and hasattr(profile_image, 'filename') and profile_image.filename:
            allowed_extensions = {'.jpg', '.jpeg', '.png', '.webp'}
            file_ext = os.path.splitext(profile_image.filename)[1].lower()
            if file_ext in allowed_extensions:
                upload_dir = Path(f"static/uploads/{company_id}/admins/{user_id}")
                upload_dir.mkdir(parents=True, exist_ok=True)
                unique_filename = f"profile-{uuid.uuid4()}{file_ext}"
                file_path = upload_dir / unique_filename
                contents = await profile_image.read()
                if len(contents) <= 2 * 1024 * 1024:  # 2MB limit
                    with open(file_path, "wb") as f:
                        f.write(contents)
                    if admin:
                        admin.profile_image_path = f"/static/uploads/{company_id}/admins/{user_id}/{unique_filename}"
    
    try:
        db.commit()
        return {"success": True, "message": "Profile updated successfully"}
    except Exception as e:
        db.rollback()
        return {"error": str(e)}

@app.post("/api/profile/upload-image")
async def upload_profile_image(request: Request, db: Session = Depends(get_db)):
    """Upload profile image"""
    auth_check = require_auth(request)
    if auth_check:
        return {"error": "Unauthorized"}
    
    user_id = request.session.get("user_id")
    company_id = request.session.get("company_id")
    
    from database import Admin
    import os
    import uuid
    from pathlib import Path
    
    form_data = await request.form()
    profile_image = form_data.get("profile_image")
    
    if not profile_image or not hasattr(profile_image, 'filename'):
        return {"error": "No image file provided"}
    
    # Validate file type
    allowed_extensions = {'.jpg', '.jpeg', '.png', '.webp'}
    file_ext = os.path.splitext(profile_image.filename)[1].lower()
    if file_ext not in allowed_extensions:
        return {"error": "Invalid file type. Only JPG, PNG, and WebP are allowed"}
    
    upload_dir = Path(f"/var/www/autoispbilling/uploads/{company_id}/admins/{user_id}")
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate unique filename
    unique_filename = f"profile-{uuid.uuid4()}{file_ext}"
    file_path = upload_dir / unique_filename
    
    # Save file
    try:
        contents = await profile_image.read()
        
        if len(contents) > 2 * 1024 * 1024:
            return {"error": "File size must be less than 2MB"}
        
        with open(file_path, "wb") as f:
            f.write(contents)
        
        relative_path = f"/uploads/{company_id}/admins/{user_id}/{unique_filename}"
        admin = db.query(Admin).filter(Admin.admin_id == user_id).first()
        if admin:
            admin.profile_image_path = relative_path
            db.commit()
        
        return {"success": True, "image_path": relative_path}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/customers/create")
async def create_customer(request: Request, db: Session = Depends(get_db)):
    """Create new customer"""
    auth_check = require_auth(request)
    if auth_check:
        return {"error": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Customer
    import bcrypt
    import json
    
    try:
        data = await request.json()
        
        # Validate required fields
        required_fields = ['customer_id', 'service_type', 'username', 'name', 'mobile', 'password']
        for field in required_fields:
            if not data.get(field):
                return {"success": False, "message": f"Missing required field: {field}"}
        
        existing = db.query(Customer).filter(Customer.customer_id == data['customer_id']).first()
        if existing:
            return {"success": False, "message": "Customer ID already exists"}
        
        password_hash = bcrypt.hashpw(data['password'].encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        def parse_float(value):
            if not value or value == '':
                return None
            try:
                clean_value = str(value).replace('₹', '').replace(',', '').strip()
                return float(clean_value)
            except:
                return None
        
        def parse_int(value):
            if not value or value == '':
                return None
            try:
                return int(value)
            except:
                return None
        
        customer = Customer(
            company_id=company_id,
            customer_id=data['customer_id'],
            password_hash=password_hash,
            registration_type=data.get('registration_type', 'New Customer'),
            service_type=data['service_type'],
            username=data['username'],
            customer_name=data['name'],
            nickname=data.get('nickname'),
            customer_email=data.get('email'),
            customer_phone=data['mobile'],
            alt_mobile=data.get('alt_mobile'),
            gst_invoice_needed=data.get('gst_invoice_needed', 'NO'),
            customer_gst_no=data.get('customer_gst_no'),
            id_proof=data.get('id_proof'),
            id_proof_no=data.get('id_proof_no'),
            installation_date=data.get('installation_date'),
            address=data.get('address'),
            locality=data.get('locality'),
            city=data.get('city'),
            state=data.get('state'),
            pincode=data.get('pincode'),
            plan_id=parse_int(data.get('plan')),
            monthly_amount=parse_float(data.get('monthly_amount')),
            auto_renew=data.get('auto_renew', 'Yes'),
            customer_type=data.get('customer_type', 'Postpaid'),
            caf_no=data.get('caf_no'),
            mac_address=data.get('mac_address'),
            ip_address=data.get('ip_address'),
            vendor=data.get('vendor'),
            modem_no=data.get('modem_no'),
            start_date=data.get('start_date'),
            period=parse_int(data.get('period')),
            end_date=data.get('end_date'),
            bill_amount=parse_float(data.get('bill_amount')),
            cgst_tax=parse_float(data.get('cgst_tax')),
            sgst_tax=parse_float(data.get('sgst_tax')),
            igst_tax=parse_float(data.get('igst_tax')),
            total_bill_amount=parse_float(data.get('total_bill_amount')),
            payment_mode=data.get('payment_mode'),
            received_amount=parse_float(data.get('received_amount')),
            security_deposit=parse_float(data.get('security_deposit')),
            installation_charges=parse_float(data.get('installation_charges')),
            router_charges=parse_float(data.get('router_charges')),
            discount_credit=parse_float(data.get('discount_credit')) or 0.0,
            transaction_id=data.get('transaction_id'),
            payment_notes=data.get('payment_notes'),
            status='Active'
        )
        
        db.add(customer)
        db.commit()
        db.refresh(customer)
        
        caf_pdf_data = None
        
        try:
            from database import Company, Plan, Invoice, Transaction, Payment, ReceivedTracker
            from datetime import timedelta
            import os
            
            company = db.query(Company).filter(Company.company_id == company_id).first()
            plan = db.query(Plan).filter(Plan.id == customer.plan_id).first() if customer.plan_id else None
            plan_name = plan.plan_name if plan else 'Broadband'
            
            is_postpaid = (customer.customer_type or 'PREPAID').upper() == 'POSTPAID'
            is_first_invoice = True  # Always true for new customer
            
            invoice_no = generate_invoice_number(company_id, db)
            issue_date = datetime.now()
            due_date = issue_date + timedelta(days=7)
            
            period_months = customer.period or 1
            
            security_deposit = customer.security_deposit or 0
            installation_charges = customer.installation_charges or 0
            router_charges = customer.router_charges or 0
            discount_credit = customer.discount_credit or 0
            
            line_items = []
            
            if is_postpaid and is_first_invoice:
                base_amount = customer.bill_amount or 0
                
                tax_breakdown = compute_tax_breakdown(
                    company.state if company else '',
                    customer.state or '',
                    base_amount
                )
                cgst_tax = tax_breakdown['cgst_tax']
                sgst_tax = tax_breakdown['sgst_tax']
                igst_tax = tax_breakdown['igst_tax']
                plan_total = tax_breakdown['total_amount']
                
                line_items.append({
                    'description': f"{plan_name.upper()}\nPlan Security",
                    'hsn_sac': '998422',
                    'quantity': '1 nos',
                    'rate': int(base_amount),
                    'amount': int(plan_total)
                })
                
                if installation_charges > 0:
                    line_items.append({
                        'description': 'Installation Charges',
                        'hsn_sac': '998422',
                        'quantity': '1 nos',
                        'rate': int(installation_charges),
                        'amount': int(installation_charges)
                    })
                
                if router_charges > 0:
                    line_items.append({
                        'description': 'STB/MODEM/Router Deposit',
                        'hsn_sac': '998422',
                        'quantity': '1 nos',
                        'rate': int(router_charges),
                        'amount': int(router_charges)
                    })
                
                if security_deposit > 0:
                    line_items.append({
                        'description': 'Security Deposit',
                        'hsn_sac': '998422',
                        'quantity': '1 nos',
                        'rate': int(security_deposit),
                        'amount': int(security_deposit)
                    })
                
                if discount_credit > 0:
                    line_items.append({
                        'description': 'Discount',
                        'hsn_sac': '998422',
                        'quantity': '1 nos',
                        'rate': -int(discount_credit),
                        'amount': -int(discount_credit)
                    })
                
                total_amount = plan_total + installation_charges + router_charges + security_deposit - discount_credit
                
            else:
                base_amount = customer.bill_amount or 0
                
                tax_breakdown = compute_tax_breakdown(
                    company.state if company else '',
                    customer.state or '',
                    base_amount
                )
                
                cgst_tax = tax_breakdown['cgst_tax']
                sgst_tax = tax_breakdown['sgst_tax']
                igst_tax = tax_breakdown['igst_tax']
                plan_total = tax_breakdown['total_amount']
                
                start_date_formatted = format_date_ddmmyyyy(customer.start_date) if customer.start_date else ''
                end_date_formatted = format_date_ddmmyyyy(customer.end_date) if customer.end_date else ''
                if start_date_formatted and end_date_formatted:
                    period_description = f"{plan_name.upper()}\nPERIOD {start_date_formatted} TO {end_date_formatted}"
                else:
                    period_description = f"{plan_name.upper()}\nPERIOD {period_months} Month(s)"
                
                line_items.append({
                    'description': period_description,
                    'hsn_sac': '998422',
                    'quantity': '1 nos',
                    'rate': int(base_amount),
                    'amount': int(plan_total)
                })
                
                if installation_charges > 0:
                    line_items.append({
                        'description': 'Installation Charges',
                        'hsn_sac': '998422',
                        'quantity': '1 nos',
                        'rate': int(installation_charges),
                        'amount': int(installation_charges)
                    })
                
                if security_deposit > 0:
                    line_items.append({
                        'description': 'Security Deposit',
                        'hsn_sac': '998422',
                        'quantity': '1 nos',
                        'rate': int(security_deposit),
                        'amount': int(security_deposit)
                    })
                
                if router_charges > 0:
                    line_items.append({
                        'description': 'STB/MODEM/Router Deposit',
                        'hsn_sac': '998422',
                        'quantity': '1 nos',
                        'rate': int(router_charges),
                        'amount': int(router_charges)
                    })
                
                if discount_credit > 0:
                    line_items.append({
                        'description': 'Discount',
                        'hsn_sac': '998422',
                        'quantity': '1 nos',
                        'rate': -int(discount_credit),
                        'amount': -int(discount_credit)
                    })
                
                total_amount = plan_total + installation_charges + router_charges + security_deposit - discount_credit
            
            payment_received = customer.received_amount or 0
            if payment_received >= total_amount:
                invoice_status = 'paid'
            elif payment_received > 0:
                invoice_status = 'partial'
            else:
                invoice_status = 'generated'
            
            invoice = Invoice(
                company_id=company_id,
                customer_id=customer.customer_id,
                invoice_no=invoice_no,
                issue_date=issue_date.strftime('%Y-%m-%d'),
                due_date=due_date.strftime('%Y-%m-%d'),
                start_date=customer.start_date or issue_date.strftime('%Y-%m-%d'),
                end_date=customer.end_date or due_date.strftime('%Y-%m-%d'),
                period_months=period_months if not (is_postpaid and is_first_invoice) else 0,
                plan_id=customer.plan_id,
                plan_name=plan_name,
                base_amount=base_amount,
                cgst_tax=cgst_tax,
                sgst_tax=sgst_tax,
                igst_tax=igst_tax,
                total_amount=total_amount,
                status=invoice_status
            )
            db.add(invoice)
            db.flush()
            
            print(f"✓ Invoice created: {invoice_no} (Type: {'POSTPAID' if is_postpaid else 'PREPAID'}, First: {is_first_invoice}, Total: ₹{total_amount}, Status: {invoice_status})")
            
            invoice_data = {
                'invoice_no': invoice_no,
                'issue_date': invoice.issue_date,
                'due_date': invoice.due_date,
                'start_date': invoice.start_date,
                'end_date': invoice.end_date,
                'plan_name': plan_name,
                'period_months': period_months if not (is_postpaid and is_first_invoice) else 0,
                'base_amount': base_amount,
                'cgst_tax': cgst_tax,
                'sgst_tax': sgst_tax,
                'igst_tax': igst_tax,
                'total_amount': total_amount,
                'customer_name': customer.customer_name,
                'prev_due_total': 0,
                'line_items': line_items,
                'payment_received': payment_received,
                'is_first_invoice': is_first_invoice,
                'is_postpaid': is_postpaid
            }
            
            company_data = {
                'company_name': company.company_name if company else 'AUTO ISP BILLING',
                'company_address': company.company_address if company else '',
                'company_phone': company.company_phone if company else '',
                'company_email': company.company_email if company else '',
                'state': company.state if company else '',
                'gst_number': company.gst_number if company else '',
                'bank_name': company.bank_name if company else '',
                'account_number': company.account_number if company else '',
                'branch_ifsc': company.branch_ifsc if company else '',
                'upi_id': company.upi_id if company else '',
                'declaration': company.declaration if company else 'Thanks for your business. Hope you are enjoying our services.',
                'terms_conditions': company.terms_conditions if company else 'Payment is due within 7 days of invoice date.',
                'smtp_server': company.smtp_server if company else '',
                'smtp_port': company.smtp_port if company else 587,
                'smtp_username': company.smtp_username if company else '',
                'smtp_password': company.smtp_password if company else '',
                'bank_qr_code': getattr(company, 'bank_qr_code', None) if company else None
            }
            
            customer_data = {
                'customer_name': customer.customer_name,
                'username': customer.username,
                'address': customer.address or '',
                'customer_gst_no': customer.customer_gst_no or '',
                'state': customer.state or '',
                'city': customer.city or '',
                'mobile': customer.customer_phone or '',
                'billing_type': customer.customer_type or 'PREPAID',
                'category': customer.service_type or 'Broadband',
                'gst_invoice_needed': customer.gst_invoice_needed or 'No'
            }
            
            pdf_data = generate_invoice_pdf(invoice_data, company_data, customer_data, [])
            
            pdf_dir = f"/var/lib/autoispbilling/invoices/{company_id}"
            os.makedirs(pdf_dir, exist_ok=True)
            pdf_path = f"{pdf_dir}/{invoice_no}.pdf"
            
            with open(pdf_path, 'wb') as f:
                f.write(pdf_data)
            
            invoice.pdf_path = pdf_path
            
            all_unpaid_invoices = db.query(Invoice).filter(
                Invoice.company_id == company_id,
                Invoice.customer_id == customer.customer_id,
                Invoice.status.in_(['generated', 'overdue', 'partial'])
            ).all()
            
            total_due = sum(inv.total_amount for inv in all_unpaid_invoices)
            customer.total_bill_amount = total_due
            
            # DO NOT zero out security_deposit, installation_charges, router_charges
            
            transaction = Transaction(
                company_id=company_id,
                customer_id=customer.customer_id,
                transaction_type='renewal',
                amount=total_amount,
                invoice_id=invoice.id,
                start_date=invoice.start_date,
                end_date=invoice.end_date,
                period_months=period_months if not (is_postpaid and is_first_invoice) else 0,
                remarks=f"Initial invoice for {period_months} month(s)" if not (is_postpaid and is_first_invoice) else "Initial invoice - Plan Security for Postpaid Users"
            )
            db.add(transaction)
            db.commit()
            
            print(f"✓ Invoice created: {invoice_no} for ₹{total_amount}")
            
            if customer.received_amount and customer.received_amount > 0:
                try:
                    payment_mode = customer.payment_mode or 'Cash'
                    transaction_no = generate_transaction_no(payment_mode, db)
                    
                    payment = Payment(
                        company_id=company_id,
                        customer_id=customer.customer_id,
                        employee_id=request.session.get("admin_id", "ADMIN001"),
                        amount=customer.received_amount,
                        discount=0.0,
                        payment_mode=payment_mode,
                        transaction_no=transaction_no,
                        paid_at=datetime.now(),
                        remarks=customer.payment_notes or "Initial payment at customer registration"
                    )
                    db.add(payment)
                    db.flush()
                    
                    received_tracker = db.query(ReceivedTracker).filter(
                        ReceivedTracker.company_id == company_id,
                        ReceivedTracker.customer_id == customer.customer_id
                    ).first()
                    
                    if received_tracker:
                        received_tracker.received_since_reset += customer.received_amount
                        received_tracker.updated_at = datetime.now()
                    else:
                        received_tracker = ReceivedTracker(
                            company_id=company_id,
                            customer_id=customer.customer_id,
                            received_since_reset=customer.received_amount,
                            last_reset_at=datetime.utcnow(),
                            updated_at=datetime.utcnow()
                        )
                        db.add(received_tracker)
                    
                    db.commit()
                    print(f"✓ Payment created: {transaction_no} for ₹{customer.received_amount}")
                except Exception as payment_error:
                    print(f"Failed to create payment: {str(payment_error)}")
                    db.rollback()
            
            reg_type = (customer.registration_type or 'New Customer').strip().lower()
            if customer.customer_email and reg_type == 'new customer':
                try:
                    email_result = await send_invoice_email(invoice_data, customer.customer_email, company_data, pdf_data, customer.customer_type or 'PREPAID')
                    if email_result and email_result.get('success'):
                        print(f"✓ Invoice email sent to {customer.customer_email}")
                    else:
                        print(f"✗ Failed to send invoice email: {email_result.get('message', 'Unknown error') if email_result else 'No result returned'}")
                except Exception as email_error:
                    print(f"✗ Exception sending invoice email: {str(email_error)}")
                    import traceback
                    traceback.print_exc()
            elif customer.customer_email:
                print(f"⊘ Invoice email skipped for migrated customer (registration_type={customer.registration_type!r}, customer_id={customer.customer_id}, email={customer.customer_email})")
            else:
                print(f"⊘ No email address provided for customer {customer.customer_id}")
            
            caf_customer_data = {
                'caf_no': customer.caf_no or '',
                'customer_id': customer.customer_id or '',
                'installation_date': customer.installation_date or '',
                'customer_name': customer.customer_name or '',
                'username': customer.username or '',
                'customer_email': customer.customer_email or '',
                'customer_phone': customer.customer_phone or '',
                'alt_mobile': customer.alt_mobile or '',
                'id_proof': customer.id_proof or '',
                'id_proof_no': customer.id_proof_no or '',
                'address': customer.address or '',
                'locality': customer.locality or '',
                'city': customer.city or '',
                'state': customer.state or '',
                'pincode': customer.pincode or '',
                'service_type': customer.service_type or '',
                'plan_name': plan_name or '',
                'customer_type': customer.customer_type or '',
                'monthly_amount': customer.monthly_amount or 0,
                'start_date': customer.start_date or '',
                'end_date': customer.end_date or '',
                'mac_address': customer.mac_address or '',
                'ip_address': customer.ip_address or ''
            }
            
            caf_pdf_data = generate_caf_pdf(caf_customer_data, company_data)
            
            customer.caf_pdf = caf_pdf_data
            db.commit()
        
        except Exception as invoice_error:
            print(f"Failed to generate/send invoice/CAF: {str(invoice_error)}")
        
        return {"success": True, "message": "Customer created successfully", "customer_id": data['customer_id']}
    
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

@app.get("/admin/add-customer", response_class=HTMLResponse)
async def add_customer(request: Request, db: Session = Depends(get_db)):
    """Unified add customer page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, "users")
    return templates.TemplateResponse("add_customer.html", context)

@app.get("/api/customers/check-username")
async def check_username(username: str, request: Request, db: Session = Depends(get_db)):
    """Check if username already exists for the company"""
    auth_check = require_auth(request)
    if auth_check:
        return {"exists": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Customer
    
    try:
        existing_customer = db.query(Customer).filter(
            Customer.username == username,
            Customer.company_id == company_id
        ).first()
        
        return {"exists": existing_customer is not None}
    
    except Exception as e:
        print(f"Error checking username: {str(e)}")
        return {"exists": False, "error": str(e)}

@app.get("/api/customers/deleted")
async def get_deleted_customers(request: Request, db: Session = Depends(get_db)):
    """Get list of deleted customers"""
    import sys
    print("=" * 80, file=sys.stderr, flush=True)
    print("DELETED CUSTOMERS ENDPOINT CALLED", file=sys.stderr, flush=True)
    print("=" * 80, file=sys.stderr, flush=True)
    
    auth_check = require_auth(request)
    if auth_check:
        print("AUTH CHECK FAILED", file=sys.stderr, flush=True)
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    print(f"COMPANY ID: {company_id}", file=sys.stderr, flush=True)
    
    from database import Customer
    
    try:
        deleted_customers = db.query(Customer).filter(
            Customer.company_id == company_id,
            Customer.status == "Deleted"
        ).all()
        
        print(f"DEBUG: Deleted customers query - company_id={company_id}, count={len(deleted_customers)}")
        
        customers_list = []
        for customer in deleted_customers:
            try:
                balance = calculate_total_due(customer, db)
            except Exception as e:
                print(f"DEBUG: Error calculating balance for {customer.customer_id}: {str(e)}")
                balance = '₹0'
            
            customers_list.append({
                'cust_id': customer.customer_id,
                'cust_name': customer.customer_name,
                'mobile': customer.customer_phone,
                'address': customer.address or '',
                'plan': customer.plan_id,
                'amount': float(customer.monthly_amount) if customer.monthly_amount else 0,
                'balance': balance,
                'exp_date': customer.end_date or 'N/A'
            })
        
        print(f"DEBUG: Returning {len(customers_list)} deleted customers")
        
        return {
            "success": True,
            "customers": customers_list
        }
    
    except Exception as e:
        print(f"DEBUG: Error in get_deleted_customers: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"success": False, "message": str(e)}

@app.post("/api/customers/{customer_id}/restore")
async def restore_customer(customer_id: str, request: Request, db: Session = Depends(get_db), _ = Depends(require_not_employee)):
    """Restore a deleted customer"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Customer
    
    try:
        customer = db.query(Customer).filter(
            Customer.customer_id == customer_id,
            Customer.company_id == company_id
        ).first()
        
        if not customer:
            return {"success": False, "message": "Customer not found"}
        
        if customer.status != "Deleted":
            return {"success": False, "message": "Customer is not deleted"}
        
        # Restore customer by setting status to Deactive
        customer.status = "Deactive"
        db.commit()
        
        return {
            "success": True,
            "message": f"Customer {customer.customer_name} has been restored successfully"
        }
    
    except Exception as e:
        db.rollback()
        return {"success": False, "message": f"Error restoring customer: {str(e)}"}

@app.delete("/api/customers/{customer_id}/permanent")
async def permanent_delete_customer(customer_id: str, request: Request, db: Session = Depends(get_db), _ = Depends(require_not_employee)):
    """Permanently delete a customer from the database (cannot be undone)"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Customer
    
    try:
        customer = db.query(Customer).filter(
            Customer.customer_id == customer_id,
            Customer.company_id == company_id,
            Customer.status == "Deleted"
        ).first()
        
        if not customer:
            return {"success": False, "message": "Deleted customer not found. Only deleted customers can be permanently removed."}
        
        customer_name = customer.customer_name
        
        db.delete(customer)
        db.commit()
        
        return {
            "success": True,
            "message": f"Customer {customer_name} (ID: {customer_id}) has been permanently deleted from the database"
        }
    
    except Exception as e:
        db.rollback()
        return {"success": False, "message": f"Error permanently deleting customer: {str(e)}"}
@app.get("/api/customers/{customer_id}")
async def get_customer(customer_id: str, request: Request, db: Session = Depends(get_db)):
    """Get customer data by ID"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Customer, Plan
    
    try:
        customer = db.query(Customer).filter(
            Customer.customer_id == customer_id,
            Customer.company_id == company_id
        ).first()
        
        if not customer:
            return {"success": False, "message": "Customer not found"}
        
        plan_name = None
        if customer.plan_id:
            plan = db.query(Plan).filter(
                Plan.id == customer.plan_id,
                Plan.company_id == company_id
            ).first()
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
            "status": customer.status
        }
        
        previous_due = calculate_total_due(customer, db)
        customer_data["previous_due"] = previous_due
        
        return {"success": True, "customer": customer_data}
    
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/customers/{customer_id}/transactions")
async def get_customer_transactions(customer_id: str, request: Request, db: Session = Depends(get_db)):
    """Get transaction history for a customer with balance calculations - includes BOTH invoices and payments"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Payment, Customer, Invoice, Transaction, Admin, Employee
    from datetime import datetime
    
    def parse_date_robust(date_str, has_time=False):
        """Parse date string with multiple format attempts, never fallback to now()"""
        if not date_str:
            return datetime.min
        
        if isinstance(date_str, datetime):
            return date_str
        
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%d-%m-%Y %H:%M:%S",
            "%d-%m-%Y %H:%M",
            "%d-%m-%Y",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y"
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(str(date_str), fmt)
            except:
                continue
        
        return datetime.min
    
    try:
        customer = db.query(Customer).filter(
            Customer.customer_id == customer_id,
            Customer.company_id == company_id
        ).first()
        
        if not customer:
            return {"success": False, "message": "Customer not found"}
        
        admin_map = {}
        employee_map = {}
        
        admins = db.query(Admin).filter(Admin.company_id == company_id).all()
        for admin in admins:
            admin_map[admin.admin_id] = admin.admin_name or admin.admin_id
        
        employees = db.query(Employee).filter(Employee.company_id == company_id).all()
        for emp in employees:
            employee_map[emp.employee_id] = emp.employee_name or emp.employee_id
        
        invoices = db.query(Invoice).filter(
            Invoice.customer_id == customer_id,
            Invoice.company_id == company_id
        ).order_by(Invoice.issue_date.asc(), Invoice.id.asc()).all()
        
        payments = db.query(Payment).filter(
            Payment.customer_id == customer_id,
            Payment.company_id == company_id
        ).order_by(Payment.paid_at.asc(), Payment.id.asc()).all()
        
        invoice_transaction_map = {}
        transactions_for_invoices = db.query(Transaction).filter(
            Transaction.customer_id == customer_id,
            Transaction.company_id == company_id,
            Transaction.invoice_id.isnot(None)
        ).all()
        
        for txn in transactions_for_invoices:
            if txn.invoice_id:
                invoice_transaction_map[txn.invoice_id] = txn.remarks or "Invoice for service period"
        
        all_transactions = []
        
        # Add invoices with proper remarks from Transaction table
        for invoice in invoices:
            created_at = invoice.created_at if invoice.created_at else parse_date_robust(invoice.issue_date, has_time=False)
            
            remarks = invoice_transaction_map.get(invoice.id, f"Invoice for service period")
            
            all_transactions.append({
                "type": "invoice",
                "date": created_at,
                "id": invoice.id,
                "data": invoice,
                "remarks": remarks
            })
        
        # Add payments
        for payment in payments:
            created_at = payment.created_at if payment.created_at else parse_date_robust(payment.paid_at, has_time=True)
            
            all_transactions.append({
                "type": "payment",
                "date": created_at,
                "id": payment.id,
                "data": payment,
                "remarks": payment.remarks or f"Payment via {payment.payment_mode or 'CASH'}"
            })
        
        all_transactions.sort(key=lambda x: (x["date"], x["id"]), reverse=False)
        
        running_balance = 0.0
        transactions = []
        
        for txn in all_transactions:
            if txn["type"] == "invoice":
                invoice = txn["data"]
                amount = float(invoice.total_amount) if invoice.total_amount else 0.0
                
                running_balance = running_balance + amount
                balance_after = running_balance
                
                performed_by = "Admin"
                if hasattr(invoice, 'created_by') and invoice.created_by:
                    performed_by = admin_map.get(invoice.created_by) or employee_map.get(invoice.created_by) or invoice.created_by
                
                transactions.append({
                    "type": "invoice",
                    "transaction_no": invoice.invoice_no,
                    "paid_at": txn["date"].strftime("%d-%m-%Y") if txn["date"] != datetime.min else "",
                    "amount": amount,
                    "discount": 0.0,
                    "payment_mode": "",
                    "remarks": txn["remarks"],
                    "payment_remarks": "",
                    "employee_id": "ADMIN001",
                    "performed_by": performed_by,
                    "balance_after": round(balance_after),
                    "invoice_id": invoice.id,
                    "has_invoice": True,
                    "download_url": f"/api/invoices/{invoice.id}/download",
                    "email_url": f"/api/invoices/{invoice.id}/send-email"
                })
            
            elif txn["type"] == "payment":
                payment = txn["data"]
                amount = float(payment.amount) if payment.amount else 0.0
                discount = float(payment.discount) if payment.discount else 0.0
                total_payment = amount + discount
                
                running_balance = running_balance - total_payment
                balance_after = running_balance
                
                remarks = txn["remarks"]
                if discount > 0:
                    remarks = f"{remarks} (Discount: ₹{int(discount)})"
                
                emp_id = payment.employee_id or "ADMIN001"
                performed_by = employee_map.get(emp_id) or admin_map.get(emp_id) or emp_id
                
                transactions.append({
                    "type": "payment",
                    "transaction_no": payment.transaction_no,
                    "paid_at": txn["date"].strftime("%d-%m-%Y %H:%M") if txn["date"] != datetime.min else "",
                    "amount": amount,
                    "discount": discount,
                    "payment_mode": payment.payment_mode,
                    "remarks": remarks,
                    "payment_remarks": payment.remarks or "",
                    "employee_id": payment.employee_id or "ADMIN001",
                    "performed_by": performed_by,
                    "balance_after": round(balance_after),
                    "has_invoice": False,
                    "payment_id": payment.id,
                    "download_url": f"/api/payments/{payment.id}/receipt",
                    "email_url": f"/api/payments/{payment.id}/send-email"
                })
        
        # Reverse the list to display newest first
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
                "current_balance": float(customer.total_bill_amount) if customer.total_bill_amount else 0.0
            }
        }
    
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/payments/{payment_id}/receipt")
async def generate_receipt(payment_id: int, request: Request, db: Session = Depends(get_db)):
    """Generate PDF receipt for a payment"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Payment, Customer, Company, Admin, Employee
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from io import BytesIO
    from fastapi.responses import StreamingResponse
    
    try:
        try:
            pdfmetrics.registerFont(TTFont('DejaVu', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
            pdfmetrics.registerFont(TTFont('DejaVu-Bold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
            FONT_REGULAR = 'DejaVu'
            FONT_BOLD = 'DejaVu-Bold'
        except:
            FONT_REGULAR = 'Helvetica'
            FONT_BOLD = 'Helvetica-Bold'
        
        payment = db.query(Payment).filter(
            Payment.id == payment_id,
            Payment.company_id == company_id
        ).first()
        
        if not payment:
            return {"success": False, "message": "Payment not found"}
        
        customer = db.query(Customer).filter(
            Customer.customer_id == payment.customer_id,
            Customer.company_id == company_id
        ).first()
        
        company = db.query(Company).filter(
            Company.company_id == company_id
        ).first()
        
        cashier_name = "System"
        if payment.employee_id:
            admin = db.query(Admin).filter(
                Admin.admin_id == payment.employee_id,
                Admin.company_id == company_id
            ).first()
            if admin:
                cashier_name = admin.admin_name
            else:
                employee = db.query(Employee).filter(
                    Employee.employee_id == payment.employee_id,
                    Employee.company_id == company_id
                ).first()
                if employee:
                    cashier_name = employee.employee_name
        
        initial_due = float(customer.total_bill_amount) if customer and customer.total_bill_amount else 0.0
        
        all_payments = db.query(Payment).filter(
            Payment.customer_id == payment.customer_id,
            Payment.company_id == company_id
        ).order_by(Payment.paid_at.asc()).all()
        
        running_balance = initial_due
        balance_before = initial_due
        balance_after = initial_due
        
        for p in all_payments:
            amount = float(p.amount) if p.amount else 0.0
            discount = float(p.discount) if p.discount else 0.0
            total_payment = amount + discount
            
            if p.id == payment.id:
                balance_before = running_balance
                running_balance = running_balance - total_payment
                balance_after = running_balance
                break
            else:
                running_balance = running_balance - total_payment
        
        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter
        
        y_position = height - 1 * inch
        
        c.setFont(FONT_BOLD, 20)
        c.drawCentredString(width / 2, y_position, company.company_name if company else "Auto ISP Billing")
        y_position -= 0.3 * inch
        
        c.setFont(FONT_REGULAR, 10)
        if company and company.company_address:
            c.drawCentredString(width / 2, y_position, company.company_address)
            y_position -= 0.2 * inch
        
        if company and company.company_phone:
            c.drawCentredString(width / 2, y_position, f"Contact: {company.company_phone}")
            y_position -= 0.4 * inch
        
        c.setFont(FONT_BOLD, 16)
        c.drawCentredString(width / 2, y_position, "Payment Receipt")
        y_position -= 0.5 * inch
        
        c.setFont(FONT_REGULAR, 11)
        c.drawString(1 * inch, y_position, f"Dear {customer.customer_name if customer else 'Customer'}")
        y_position -= 0.2 * inch
        c.drawString(1 * inch, y_position, "Details of your transaction are given below:")
        y_position -= 0.4 * inch
        
        c.line(1 * inch, y_position, width - 1 * inch, y_position)
        y_position -= 0.3 * inch
        
        receipt_data = [
            ("Customer ID", payment.customer_id),
            ("Total Due Balance (Before Payment)", f"₹{round(balance_before)}"),
            ("Amount Paid", f"₹{round(payment.amount)}"),
            ("Discount", f"₹{round(payment.discount)}" if payment.discount else "₹0"),
            ("Balance After Payment", f"₹{round(balance_after)}"),
            ("Transaction ID", payment.transaction_no),
            ("Date & Time", payment.paid_at.strftime("%d %b %Y, %I:%M %p") if payment.paid_at else ""),
            ("Payment Mode", payment.payment_mode),
            ("Payment Remark", payment.remarks or "-"),
            ("Cashier", cashier_name)
        ]
        
        label_x = 1 * inch
        value_x = width - 1 * inch
        
        for label, value in receipt_data:
            c.setFont(FONT_BOLD, 10)
            c.drawString(label_x, y_position, label)
            c.setFont(FONT_REGULAR, 10)
            c.drawRightString(value_x, y_position, str(value))
            y_position -= 0.35 * inch
        
        y_position -= 0.2 * inch
        c.line(1 * inch, y_position, width - 1 * inch, y_position)
        y_position -= 0.3 * inch
        
        c.setFont(FONT_REGULAR, 9)
        c.drawString(1 * inch, y_position, "This will be credited into your account. Please quote your transaction ID for any queries")
        y_position -= 0.15 * inch
        c.drawString(1 * inch, y_position, "related to this transaction.")
        y_position -= 0.3 * inch
        
        c.setFont(FONT_REGULAR, 9)
        c.drawString(1 * inch, y_position, "For more details, visit: www.autoispbilling.com")
        
        c.save()
        buffer.seek(0)
        
        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=receipt_{payment.transaction_no}.pdf"
            }
        )
    
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.post("/api/payments/{payment_id}/send-email")
async def send_receipt_email(payment_id: int, request: Request, db: Session = Depends(get_db)):
    """Send receipt via email with PDF attachment"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Payment, Customer, Company, Admin, Employee
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from io import BytesIO
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.application import MIMEApplication
    
    try:
        try:
            pdfmetrics.registerFont(TTFont('DejaVu', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
            pdfmetrics.registerFont(TTFont('DejaVu-Bold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
            FONT_REGULAR = 'DejaVu'
            FONT_BOLD = 'DejaVu-Bold'
        except:
            FONT_REGULAR = 'Helvetica'
            FONT_BOLD = 'Helvetica-Bold'
        
        payment = db.query(Payment).filter(
            Payment.id == payment_id,
            Payment.company_id == company_id
        ).first()
        
        if not payment:
            return {"success": False, "message": "Payment not found"}
        
        customer = db.query(Customer).filter(
            Customer.customer_id == payment.customer_id,
            Customer.company_id == company_id
        ).first()
        
        if not customer or not customer.customer_email:
            return {"success": False, "message": "Customer email not available"}
        
        company = db.query(Company).filter(
            Company.company_id == company_id
        ).first()
        
        cashier_name = "System"
        if payment.employee_id:
            admin = db.query(Admin).filter(
                Admin.admin_id == payment.employee_id,
                Admin.company_id == company_id
            ).first()
            if admin:
                cashier_name = admin.admin_name
            else:
                employee = db.query(Employee).filter(
                    Employee.employee_id == payment.employee_id,
                    Employee.company_id == company_id
                ).first()
                if employee:
                    cashier_name = employee.employee_name
        
        initial_due = float(customer.total_bill_amount) if customer and customer.total_bill_amount else 0.0
        
        all_payments = db.query(Payment).filter(
            Payment.customer_id == payment.customer_id,
            Payment.company_id == company_id
        ).order_by(Payment.paid_at.asc()).all()
        
        running_balance = initial_due
        balance_before = initial_due
        balance_after = initial_due
        
        for p in all_payments:
            amount = float(p.amount) if p.amount else 0.0
            discount = float(p.discount) if p.discount else 0.0
            total_payment = amount + discount
            
            if p.id == payment.id:
                balance_before = running_balance
                running_balance = running_balance - total_payment
                balance_after = running_balance
                break
            else:
                running_balance = running_balance - total_payment
        
        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter
        
        y_position = height - 1 * inch
        
        c.setFont(FONT_BOLD, 20)
        c.drawCentredString(width / 2, y_position, company.company_name if company else "Auto ISP Billing")
        y_position -= 0.3 * inch
        
        c.setFont(FONT_REGULAR, 10)
        if company and company.company_address:
            c.drawCentredString(width / 2, y_position, company.company_address)
            y_position -= 0.2 * inch
        
        if company and company.company_phone:
            c.drawCentredString(width / 2, y_position, f"Contact: {company.company_phone}")
            y_position -= 0.4 * inch
        
        c.setFont(FONT_BOLD, 16)
        c.drawCentredString(width / 2, y_position, "Payment Receipt")
        y_position -= 0.5 * inch
        
        c.setFont(FONT_REGULAR, 11)
        c.drawString(1 * inch, y_position, f"Dear {customer.customer_name if customer else 'Customer'}")
        y_position -= 0.2 * inch
        c.drawString(1 * inch, y_position, "Details of your transaction are given below:")
        y_position -= 0.4 * inch
        
        c.line(1 * inch, y_position, width - 1 * inch, y_position)
        y_position -= 0.3 * inch
        
        receipt_data = [
            ("Customer ID", payment.customer_id),
            ("Total Due Balance (Before Payment)", f"₹{round(balance_before)}"),
            ("Amount Paid", f"₹{round(payment.amount)}"),
            ("Discount", f"₹{round(payment.discount)}" if payment.discount else "₹0"),
            ("Balance After Payment", f"₹{round(balance_after)}"),
            ("Transaction ID", payment.transaction_no),
            ("Date & Time", payment.paid_at.strftime("%d %b %Y, %I:%M %p") if payment.paid_at else ""),
            ("Payment Mode", payment.payment_mode),
            ("Payment Remark", payment.remarks or "-"),
            ("Cashier", cashier_name)
        ]
        
        label_x = 1 * inch
        value_x = width - 1 * inch
        
        for label, value in receipt_data:
            c.setFont(FONT_BOLD, 10)
            c.drawString(label_x, y_position, label)
            c.setFont(FONT_REGULAR, 10)
            c.drawRightString(value_x, y_position, str(value))
            y_position -= 0.35 * inch
        
        y_position -= 0.2 * inch
        c.line(1 * inch, y_position, width - 1 * inch, y_position)
        y_position -= 0.3 * inch
        
        c.setFont(FONT_REGULAR, 9)
        c.drawString(1 * inch, y_position, "This will be credited into your account. Please quote your transaction ID for any queries")
        y_position -= 0.15 * inch
        c.drawString(1 * inch, y_position, "related to this transaction.")
        y_position -= 0.3 * inch
        
        c.setFont(FONT_REGULAR, 9)
        c.drawString(1 * inch, y_position, "For more details, visit: www.autoispbilling.com")
        
        c.save()
        buffer.seek(0)
        pdf_data = buffer.read()
        
        msg = MIMEMultipart()
        msg['From'] = "no-reply@autoispbilling.com"
        msg['To'] = customer.customer_email
        msg['Subject'] = f"Payment Receipt - {payment.transaction_no}"
        
        body = f"""Dear {customer.customer_name},

Thank you for your payment. Please find attached your payment receipt.

Transaction Details:
- Transaction ID: {payment.transaction_no}
- Amount Paid: ₹{round(payment.amount)}
- Date & Time: {payment.paid_at.strftime("%d %b %Y, %I:%M %p") if payment.paid_at else ""}
- Payment Mode: {payment.payment_mode}

If you have any questions, please contact us.

Best regards,
{company.company_name if company else "Auto ISP Billing"}
"""
        
        msg.attach(MIMEText(body, 'plain'))
        
        pdf_attachment = MIMEApplication(pdf_data, _subtype='pdf')
        pdf_attachment.add_header('Content-Disposition', 'attachment', filename=f'receipt_{payment.transaction_no}.pdf')
        msg.attach(pdf_attachment)
        
        smtp_server = "smtp.hostinger.com"
        smtp_port = 465
        smtp_username = "no-reply@autoispbilling.com"
        smtp_password = "Login@121212"
        
        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
        
        return {
            "success": True, 
            "message": f"Receipt sent successfully to {customer.customer_email}"
        }
    
    except Exception as e:
        return {"success": False, "message": f"Failed to send email: {str(e)}"}

@app.post("/api/customers/{customer_id}/send-payment-link")
async def send_payment_link_email(customer_id: str, request: Request, db: Session = Depends(get_db)):
    """Send payment link/reminder via email directly with QR code"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Customer, Company
    import smtplib
    import os
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.image import MIMEImage
    
    try:
        customer = db.query(Customer).filter(
            Customer.customer_id == customer_id,
            Customer.company_id == company_id
        ).first()
        
        if not customer:
            return {"success": False, "message": "Customer not found"}
        
        customer_email = (customer.customer_email or '').strip()
        if not customer_email:
            print(f"Customer email not available: customer_id={customer_id}, company_id={company_id}, raw_email={repr(customer.customer_email)}")
            return {"success": False, "message": "Customer email not available"}
        
        company = db.query(Company).filter(
            Company.company_id == company_id
        ).first()
        
        if not company:
            return {"success": False, "message": "Company not found"}
        
        if not all([company.smtp_server, company.smtp_port, company.smtp_username, company.smtp_password]):
            return {"success": False, "message": "SMTP not configured. Please configure email settings in company profile."}
        
        pending_amount = calculate_total_due(customer, db)
        
        subject = f"Payment Reminder - {customer_id}"
        
        msg = MIMEMultipart('related')
        msg['From'] = company.smtp_username
        msg['To'] = customer_email
        msg['Subject'] = subject
        
        msg_alternative = MIMEMultipart('alternative')
        msg.attach(msg_alternative)
        
        text_body = f"""Dear {customer.customer_name},

Your payment is pending for Customer ID: {customer_id}

Total Amount Due: ₹{pending_amount}

Please make the payment at your earliest convenience to continue enjoying uninterrupted services.

Scan QR code to pay (if available).

Thank you for your business!

Best regards,
{company.company_name}
{company.company_phone}
{company.company_email}
"""
        
        html_body = f"""<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
    <p>Dear {customer.customer_name},</p>
    
    <p>Your payment is pending for Customer ID: <strong>{customer_id}</strong></p>
    
    <p style="font-size: 18px; color: #d9534f;"><strong>Total Amount Due: ₹{pending_amount}</strong></p>
    
    <p>Please make the payment at your earliest convenience to continue enjoying uninterrupted services.</p>
    
    <div style="text-align: center; margin: 20px 0;">
        <p style="font-weight: bold; margin-bottom: 10px;">Scan to Pay:</p>
        <img src="cid:payment_qr" alt="Payment QR Code" style="max-width: 200px; height: auto; border: 2px solid #ddd; padding: 10px;">
    </div>
    
    <p>Thank you for your business!</p>
    
    <p style="margin-top: 20px;">
        Best regards,<br>
        <strong>{company.company_name}</strong><br>
        {company.company_phone}<br>
        {company.company_email}
    </p>
</body>
</html>"""
        
        qr_path = getattr(company, 'bank_qr_code', None)
        if qr_path and qr_path.startswith('/static/'):
            qr_path = qr_path.lstrip('/')
        has_qr = bool(qr_path and os.path.exists(qr_path))
        
        # Only include QR section in HTML if QR code exists
        if has_qr:
            html_body_with_qr = html_body  # HTML already includes QR section
        else:
            html_body_with_qr = html_body.replace(
                '''    <div style="text-align: center; margin: 20px 0;">
        <p style="font-weight: bold; margin-bottom: 10px;">Scan to Pay:</p>
        <img src="cid:payment_qr" alt="Payment QR Code" style="max-width: 200px; height: auto; border: 2px solid #ddd; padding: 10px;">
    </div>
    ''', '')
            text_body = text_body.replace('\nScan QR code to pay (if available).\n', '\n')
        
        msg_alternative.attach(MIMEText(text_body, 'plain'))
        msg_alternative.attach(MIMEText(html_body_with_qr, 'html'))
        
        if has_qr:
            try:
                with open(qr_path, 'rb') as qr_file:
                    qr_image = MIMEImage(qr_file.read())
                    qr_image.add_header('Content-ID', '<payment_qr>')
                    qr_image.add_header('Content-Disposition', 'inline', filename='payment_qr.png')
                    msg.attach(qr_image)
            except Exception as qr_error:
                print(f"Warning: Could not attach QR code to payment link email: {str(qr_error)}")
        
        if int(company.smtp_port) == 465:
            with smtplib.SMTP_SSL(company.smtp_server, int(company.smtp_port)) as server:
                server.login(company.smtp_username, company.smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(company.smtp_server, int(company.smtp_port)) as server:
                server.starttls()
                server.login(company.smtp_username, company.smtp_password)
                server.send_message(msg)
        
        return {
            "success": True,
            "message": f"Payment reminder sent successfully to {customer_email}"
        }
    
    except Exception as e:
        print(f"Error sending payment link email: {str(e)}")
        return {"success": False, "message": f"Failed to send email: {str(e)}"}

@app.get("/api/invoices/{invoice_id}/download")
async def download_invoice(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    """Download invoice PDF"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Invoice, Customer, Company
    from fastapi.responses import StreamingResponse
    from io import BytesIO
    
    try:
        invoice = db.query(Invoice).filter(
            Invoice.id == invoice_id,
            Invoice.company_id == company_id
        ).first()
        
        if not invoice:
            return {"success": False, "message": "Invoice not found"}
        
        customer = db.query(Customer).filter(
            Customer.customer_id == invoice.customer_id,
            Customer.company_id == company_id
        ).first()
        
        if not customer:
            return {"success": False, "message": "Customer not found"}
        
        company = db.query(Company).filter(
            Company.company_id == company_id
        ).first()
        
        invoice_data = {
            "invoice_no": invoice.invoice_no,
            "issue_date": invoice.issue_date,
            "due_date": invoice.due_date,
            "service_period_start": invoice.start_date,
            "service_period_end": invoice.end_date,
            "plan_name": invoice.plan_name or "",
            "base_amount": float(invoice.base_amount) if invoice.base_amount else 0.0,
            "cgst": float(invoice.cgst_tax) if invoice.cgst_tax else 0.0,
            "sgst": float(invoice.sgst_tax) if invoice.sgst_tax else 0.0,
            "igst": float(invoice.igst_tax) if invoice.igst_tax else 0.0,
            "total_amount": float(invoice.total_amount) if invoice.total_amount else 0.0,
            "billing_type": customer.customer_type or "PREPAID"
        }
        
        company_data = {
            "company_name": company.company_name if company else "Auto ISP Billing",
            "company_address": company.company_address if company else "",
            "company_phone": company.company_phone if company else "",
            "company_email": company.company_email if company else "",
            "gst_number": company.gst_number if company else "",
            "smtp_server": company.smtp_server if company else None,
            "smtp_port": company.smtp_port if company else None,
            "smtp_username": company.smtp_username if company else None,
            "smtp_password": company.smtp_password if company else None
        }
        
        customer_data = {
            "customer_id": customer.customer_id,
            "customer_name": customer.customer_name,
            "customer_email": customer.customer_email,
            "customer_phone": customer.customer_phone,
            "address": customer.address
        }
        
        if invoice.pdf_path and os.path.exists(invoice.pdf_path):
            with open(invoice.pdf_path, 'rb') as f:
                pdf_data = f.read()
        else:
            pdf_data = generate_invoice_pdf(invoice_data, company_data, customer_data, [])
        
        buffer = BytesIO(pdf_data)
        buffer.seek(0)
        
        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=invoice_{invoice.invoice_no}.pdf"
            }
        )
    
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.post("/api/invoices/{invoice_id}/send-email")
async def send_invoice_email_endpoint(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    """Send invoice via email"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Invoice, Customer, Company
    
    try:
        invoice = db.query(Invoice).filter(
            Invoice.id == invoice_id,
            Invoice.company_id == company_id
        ).first()
        
        if not invoice:
            return {"success": False, "message": "Invoice not found"}
        
        customer = db.query(Customer).filter(
            Customer.customer_id == invoice.customer_id,
            Customer.company_id == company_id
        ).first()
        
        if not customer or not customer.customer_email:
            return {"success": False, "message": "Customer email not available"}
        
        company = db.query(Company).filter(
            Company.company_id == company_id
        ).first()
        
        invoice_data = {
            "invoice_no": invoice.invoice_no,
            "issue_date": invoice.issue_date,
            "due_date": invoice.due_date,
            "service_period_start": invoice.start_date,
            "service_period_end": invoice.end_date,
            "plan_name": invoice.plan_name or "",
            "base_amount": float(invoice.base_amount) if invoice.base_amount else 0.0,
            "cgst": float(invoice.cgst_tax) if invoice.cgst_tax else 0.0,
            "sgst": float(invoice.sgst_tax) if invoice.sgst_tax else 0.0,
            "igst": float(invoice.igst_tax) if invoice.igst_tax else 0.0,
            "total_amount": float(invoice.total_amount) if invoice.total_amount else 0.0,
            "billing_type": customer.customer_type or "PREPAID"
        }
        
        company_data = {
            "company_name": company.company_name if company else "Auto ISP Billing",
            "company_address": company.company_address if company else "",
            "company_phone": company.company_phone if company else "",
            "company_email": company.company_email if company else "",
            "gst_number": company.gst_number if company else "",
            "smtp_server": company.smtp_server if company else None,
            "smtp_port": company.smtp_port if company else None,
            "smtp_username": company.smtp_username if company else None,
            "smtp_password": company.smtp_password if company else None
        }
        
        customer_data = {
            "customer_id": customer.customer_id,
            "customer_name": customer.customer_name,
            "customer_email": customer.customer_email,
            "customer_phone": customer.customer_phone,
            "address": customer.address
        }
        
        if invoice.pdf_path and os.path.exists(invoice.pdf_path):
            with open(invoice.pdf_path, 'rb') as f:
                pdf_data = f.read()
        else:
            pdf_data = generate_invoice_pdf(invoice_data, company_data, customer_data, [])
        
        result = await send_invoice_email(invoice_data, customer.customer_email, company_data, pdf_data, customer.customer_type or "PREPAID")
        
        if result.get("success"):
            return {"success": True, "message": f"Invoice sent successfully to {customer.customer_email}"}
        else:
            return {"success": False, "message": result.get("message", "Failed to send invoice")}
    
    except Exception as e:
        return {"success": False, "message": f"Failed to send email: {str(e)}"}

@app.post("/api/customers/update")
async def update_customer(request: Request, db: Session = Depends(get_db)):
    """Update customer data"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Customer
    
    try:
        data = await request.json()
        
        # Validate required fields
        if not data.get('customer_id'):
            return {"success": False, "message": "Customer ID is required"}
        
        customer = db.query(Customer).filter(
            Customer.customer_id == data['customer_id'],
            Customer.company_id == company_id
        ).first()
        
        if not customer:
            return {"success": False, "message": "Customer not found"}
        
        def parse_float(value):
            if not value or value == '':
                return None
            try:
                clean_value = str(value).replace('₹', '').replace(',', '').strip()
                return float(clean_value)
            except:
                return None
        
        def parse_int(value):
            if not value or value == '':
                return None
            try:
                return int(value)
            except:
                return None
        
        # Update customer fields
        customer.service_type = data.get('service_type', customer.service_type)
        customer.customer_name = data.get('name', customer.customer_name)
        customer.username = data.get('username', customer.username)
        customer.customer_email = data.get('email', customer.customer_email)
        customer.customer_phone = data.get('mobile', customer.customer_phone)
        customer.alt_mobile = data.get('alt_mobile')
        customer.gst_invoice_needed = data.get('gst_invoice_needed', 'NO')
        customer.customer_gst_no = data.get('customer_gst_no')
        customer.id_proof = data.get('id_proof')
        customer.id_proof_no = data.get('id_proof_no')
        customer.installation_date = data.get('installation_date')
        customer.address = data.get('address')
        customer.locality = data.get('locality')
        customer.state = data.get('state')
        customer.city = data.get('city')
        customer.pincode = data.get('pincode')
        customer.plan_id = parse_int(data.get('plan'))
        customer.monthly_amount = parse_float(data.get('monthly_amount'))
        customer.auto_renew = data.get('auto_renew', 'Yes')
        customer.customer_type = data.get('customer_type', 'Postpaid')
        customer.caf_no = data.get('caf_no')
        customer.mac_address = data.get('mac_address')
        customer.ip_address = data.get('ip_address')
        customer.vendor = data.get('vendor')
        customer.modem_no = data.get('modem_no')
        customer.start_date = data.get('start_date')
        customer.period = parse_int(data.get('period'))
        customer.end_date = data.get('end_date')
        
        if 'bill_amount' in data:
            customer.bill_amount = parse_float(data.get('bill_amount'))
        if 'cgst_tax' in data:
            customer.cgst_tax = parse_float(data.get('cgst_tax'))
        if 'sgst_tax' in data:
            customer.sgst_tax = parse_float(data.get('sgst_tax'))
        if 'igst_tax' in data:
            customer.igst_tax = parse_float(data.get('igst_tax'))
        if 'total_bill_amount' in data:
            customer.total_bill_amount = parse_float(data.get('total_bill_amount'))
        
        customer.security_deposit = parse_float(data.get('security_deposit'))
        customer.installation_charges = parse_float(data.get('installation_charges'))
        customer.received_amount = parse_float(data.get('received_amount'))
        customer.router_charges = parse_float(data.get('router_charges'))
        
        if 'discount_credit' in data:
            discount_val = parse_float(data.get('discount_credit'))
            if discount_val is not None:
                customer.discount_credit = discount_val
        
        if 'total_due_balance' in data:
            from database import Payment
            from sqlalchemy import func
            
            total_due_balance = parse_float(data.get('total_due_balance'))
            if total_due_balance is not None:
                payment_sum = db.query(func.sum(Payment.amount)).filter(
                    Payment.customer_id == customer.customer_id,
                    Payment.company_id == company_id
                ).scalar() or 0
                
                discount_sum = db.query(func.sum(Payment.discount)).filter(
                    Payment.customer_id == customer.customer_id,
                    Payment.company_id == company_id
                ).scalar() or 0
                
                security_deposit = customer.security_deposit or 0
                installation_charges = customer.installation_charges or 0
                
                # Formula: total_due = total_bill + security_dep + installation - discount_credit - (payment_sum + discount_sum)
                # Reverse to solve for total_bill: total_bill = total_due - security_dep - installation + discount_credit + (payment_sum + discount_sum)
                # But we set discount_credit = 0, so: total_bill = total_due - security_dep - installation + (payment_sum + discount_sum)
                customer.total_bill_amount = total_due_balance - security_deposit - installation_charges + (payment_sum + discount_sum)
                customer.discount_credit = 0.0
        
        customer.payment_mode = data.get('payment_mode')
        customer.transaction_id = data.get('transaction_id')
        customer.payment_notes = data.get('payment_notes')
        
        db.commit()
        
        return {"success": True, "message": "Customer updated successfully"}
    
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

@app.delete("/api/customers/{cust_id}")
async def delete_customer(cust_id: str, request: Request, db: Session = Depends(get_db), _ = Depends(require_not_employee)):
    """Soft delete a customer by setting status to 'Deleted'"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Customer
    
    try:
        # Find customer by customer_id (string like CUST36223539) and company_id
        customer = db.query(Customer).filter(
            Customer.customer_id == cust_id,
            Customer.company_id == company_id
        ).first()
        
        if not customer:
            return {"success": False, "message": "Customer not found"}
        
        if customer.status == "Deleted":
            return {"success": False, "message": "Customer is already deleted"}
        
        customer.status = "Deleted"
        db.commit()
        
        return {"success": True, "message": f"Customer {customer.customer_name} has been deleted successfully"}
    
    except Exception as e:
        db.rollback()
        return {"success": False, "message": f"Error deleting customer: {str(e)}"}

def parse_currency(value):
    """Parse currency string safely (strip ₹, commas, spaces)"""
    if not value or value == '':
        return 0.0
    try:
        clean_value = str(value).replace('₹', '').replace(',', '').strip()
        return float(clean_value)
    except:
        return 0.0

def calculate_total_due(customer, db: Session) -> float:
    """Calculate total due for a customer using invoice-driven computation
    
    Returns raw algebraic result (can be negative for credit/advance payments)
    
    This uses compute_customer_balance which calculates:
    sum(all invoices) - sum(payments) - sum(discounts)
    
    This avoids double-counting discount_credit since discounts are already
    applied as negative line items in invoices.
    """
    return compute_customer_balance(customer.customer_id, customer.company_id, db)


def compute_customer_balance(customer_id: str, company_id: str, db: Session, exclude_invoice_no: str = None) -> float:
    """Compute customer's net balance for invoice purposes (excludes one-time charges)
    
    This calculates: sum(all invoices) - sum(payments) - sum(discounts)
    
    Args:
        customer_id: Customer ID
        company_id: Company ID
        db: Database session
        exclude_invoice_no: Optional invoice number to exclude from calculation (for computing previous balance)
    
    Returns:
        Net balance (can be negative for credit/advance payments)
    """
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
    
    net_balance = total_invoices - payment_sum - discount_sum
    return net_balance

def generate_transaction_no(payment_mode: str, db: Session) -> str:
    """Generate unique transaction number with retry logic"""
    prefixes = {
        'Cash': 'CSH',
        'Paytm': 'PTM',
        'Google Pay': 'GPY',
        'Phone Pay': 'PPY',
        'Cheque': 'CHQ',
        'Netbanking': 'NET',
        'Online Portal': 'PRT'
    }
    
    from database import Payment
    
    prefix = prefixes.get(payment_mode, 'TXN')
    
    for _ in range(10):
        # Generate 6-digit random number
        random_num = ''.join(random.choices(string.digits, k=6))
        transaction_no = f"{prefix}{random_num}"
        
        existing = db.query(Payment).filter(Payment.transaction_no == transaction_no).first()
        if not existing:
            return transaction_no
    
    raise Exception("Failed to generate unique transaction number after 10 attempts")

@app.get("/api/payments/transaction-no")
async def preview_transaction_no(request: Request, payment_mode: str = "Cash", db: Session = Depends(get_db)):
    """Generate a preview transaction number without writing to DB"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    try:
        transaction_no = generate_transaction_no(payment_mode, db)
        return {"success": True, "transaction_no": transaction_no}
    except Exception as e:
        return {"success": False, "message": str(e)}

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

@app.get("/api/employees/{employee_id}/locations")
async def get_employee_locations(employee_id: int, request: Request, db: Session = Depends(get_db)):
    """Get employee GPS tracking locations (placeholder for mobile app integration)"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Employee
    
    try:
        employee = db.query(Employee).filter(
            Employee.id == employee_id,
            Employee.company_id == company_id,
            Employee.is_deleted == False
        ).first()
        
        if not employee:
            return {"success": False, "message": "Employee not found"}
        
        locations = []
        if employee.last_latitude and employee.last_longitude:
            locations.append({
                "latitude": employee.last_latitude,
                "longitude": employee.last_longitude,
                "timestamp": employee.last_seen_at.isoformat() if employee.last_seen_at else None
            })
        
        return {
            "success": True,
            "employee_name": employee.employee_name,
            "employee_code": employee.employee_code,
            "locations": locations
        }
    
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.post("/api/payments/create")
async def create_payment(request: Request, db: Session = Depends(get_db)):
    """Create a new payment record"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Payment, Customer
    
    try:
        data = await request.json()
        
        # Validate required fields
        if not data.get('customer_id'):
            return {"success": False, "message": "Customer ID is required"}
        if not data.get('payment_mode'):
            return {"success": False, "message": "Payment mode is required"}
        if not data.get('employee_id'):
            return {"success": False, "message": "Employee is required"}
        
        paying_amount = parse_currency(data.get('paying_amount', 0))
        discount = parse_currency(data.get('discount', 0))
        
        # Validate amounts
        if paying_amount < 0:
            return {"success": False, "message": "Paying amount cannot be negative"}
        if discount < 0:
            return {"success": False, "message": "Discount cannot be negative"}
        if paying_amount + discount <= 0:
            return {"success": False, "message": "Total payment (amount + discount) must be greater than zero"}
        
        customer = db.query(Customer).filter(
            Customer.customer_id == data['customer_id'],
            Customer.company_id == company_id
        ).first()
        
        if not customer:
            return {"success": False, "message": "Customer not found"}
        
        total_bill = customer.total_bill_amount or 0
        security_dep = customer.security_deposit or 0
        installation = customer.installation_charges or 0
        discount_credit = customer.discount_credit or 0
        
        payment_sum = db.query(func.sum(Payment.amount)).filter(
            Payment.customer_id == data['customer_id'],
            Payment.company_id == company_id
        ).scalar() or 0
        
        discount_sum = db.query(func.sum(Payment.discount)).filter(
            Payment.customer_id == data['customer_id'],
            Payment.company_id == company_id
        ).scalar() or 0
        
        # Allow negative balances (credit/advance payments)
        total_due = total_bill + security_dep + installation - discount_credit - (payment_sum + discount_sum)
        
        # Parse paid_at datetime
        paid_at = None
        if data.get('paid_at'):
            try:
                paid_at = datetime.fromisoformat(data['paid_at'].replace('Z', '+00:00'))
            except:
                try:
                    paid_at = datetime.strptime(data['paid_at'], '%d-%m-%Y %H:%M')
                except:
                    paid_at = datetime.utcnow()
        else:
            paid_at = datetime.utcnow()
        
        # Generate transaction number with retry on conflict
        max_retries = 3
        for attempt in range(max_retries):
            try:
                transaction_no = generate_transaction_no(data['payment_mode'], db)
                
                payment = Payment(
                    company_id=company_id,
                    customer_id=data['customer_id'],
                    employee_id=data['employee_id'],
                    amount=paying_amount,
                    discount=discount,
                    payment_mode=data['payment_mode'],
                    transaction_no=transaction_no,
                    paid_at=paid_at,
                    remarks=data.get('remarks', '')
                )
                
                db.add(payment)
                db.commit()
                
                from database import ReceivedTracker
                received_tracker = db.query(ReceivedTracker).filter(
                    ReceivedTracker.company_id == company_id,
                    ReceivedTracker.customer_id == data['customer_id']
                ).first()
                
                if received_tracker:
                    # Only increment if payment is at or after last reset (avoid counting backdated payments)
                    if paid_at >= received_tracker.last_reset_at:
                        received_tracker.received_since_reset += (paying_amount + discount)
                        received_tracker.updated_at = datetime.utcnow()
                else:
                    received_tracker = ReceivedTracker(
                        company_id=company_id,
                        customer_id=data['customer_id'],
                        received_since_reset=(paying_amount + discount),
                        last_reset_at=datetime.utcnow(),
                        updated_at=datetime.utcnow()
                    )
                    db.add(received_tracker)
                
                db.commit()
                
                new_payment_sum = payment_sum + paying_amount
                new_discount_sum = discount_sum + discount
                # Allow negative balances (credit/advance payments)
                new_balance = total_bill + security_dep + installation - discount_credit - (new_payment_sum + new_discount_sum)
                
                if customer.status == 'Deactive' and new_balance <= 0.01:
                    customer.status = 'Active'
                    db.commit()
                
                return {
                    "success": True,
                    "message": "Payment added successfully",
                    "transaction_no": transaction_no,
                    "received_total": new_payment_sum,
                    "new_balance": new_balance,
                    "received_since_reset": received_tracker.received_since_reset,
                    "status": customer.status
                }
                
            except IntegrityError:
                db.rollback()
                if attempt == max_retries - 1:
                    return {"success": False, "message": "Failed to generate unique transaction number. Please try again."}
                continue
    
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

@app.patch("/api/customers/{customer_id}/auto-renew")
async def toggle_auto_renew(customer_id: str, request: Request, db: Session = Depends(get_db)):
    """Toggle auto-renew status for a customer"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Customer
    
    try:
        data = await request.json()
        auto_renew = data.get('auto_renew')
        
        if auto_renew is None:
            return {"success": False, "message": "auto_renew field is required"}
        
        customer = db.query(Customer).filter(
            Customer.customer_id == customer_id,
            Customer.company_id == company_id
        ).first()
        
        if not customer:
            return {"success": False, "message": "Customer not found"}
        
        customer.auto_renew = 'Yes' if auto_renew else 'No'
        db.commit()
        
        return {
            "success": True,
            "message": f"Auto-renew {'enabled' if auto_renew else 'disabled'} successfully",
            "auto_renew": customer.auto_renew
        }
    
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

@app.patch("/api/customers/{customer_id}/status")
async def toggle_customer_status(customer_id: str, request: Request, db: Session = Depends(get_db)):
    """Toggle customer active/inactive status"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Customer
    
    try:
        data = await request.json()
        active = data.get('active')
        
        if active is None:
            return {"success": False, "message": "active field is required"}
        
        customer = db.query(Customer).filter(
            Customer.customer_id == customer_id,
            Customer.company_id == company_id
        ).first()
        
        if not customer:
            return {"success": False, "message": "Customer not found"}
        
        customer.status = 'Active' if active else 'Deactive'
        db.commit()
        
        return {
            "success": True,
            "message": f"Customer {'activated' if active else 'deactivated'} successfully",
            "status": customer.status
        }
    
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

@app.patch("/api/customers/{customer_id}/end-date")
async def update_end_date(customer_id: str, request: Request, db: Session = Depends(get_db)):
    """Update customer end date"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Customer
    from datetime import datetime
    
    try:
        data = await request.json()
        end_date = data.get('end_date')
        
        if not end_date:
            return {"success": False, "message": "end_date is required"}
        
        # Validate date format
        try:
            datetime.strptime(end_date, '%Y-%m-%d')
        except ValueError:
            return {"success": False, "message": "Invalid date format. Use YYYY-MM-DD"}
        
        customer = db.query(Customer).filter(
            Customer.customer_id == customer_id,
            Customer.company_id == company_id
        ).first()
        
        if not customer:
            return {"success": False, "message": "Customer not found"}
        
        customer.end_date = end_date
        db.commit()
        
        return {
            "success": True,
            "message": "End date updated successfully",
            "end_date": customer.end_date
        }
    
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

def format_date_ddmmyyyy(date_str: str) -> str:
    """Convert YYYY-MM-DD to DD-MM-YYYY format"""
    from datetime import datetime
    if not date_str:
        return ''
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        return dt.strftime('%d-%m-%Y')
    except:
        return date_str if date_str else ''

def compute_tax_breakdown(company_state: str, customer_state: str, base_amount: float) -> dict:
    """Compute CGST, SGST, IGST based on state matching"""
    gst_rate = 0.18
    
    if company_state and customer_state and company_state.lower() == customer_state.lower():
        cgst = base_amount * (gst_rate / 2)
        sgst = base_amount * (gst_rate / 2)
        igst = 0.0
    else:
        cgst = 0.0
        sgst = 0.0
        igst = base_amount * gst_rate
    
    total_amount = base_amount + cgst + sgst + igst
    
    return {
        'cgst_tax': round(cgst),
        'sgst_tax': round(sgst),
        'igst_tax': round(igst),
        'total_amount': round(total_amount)
    }

def generate_invoice_number(company_id: str, db: Session) -> str:
    """Generate unique random 8-digit invoice number for a company"""
    from database import Invoice
    import random
    
    max_attempts = 50
    for attempt in range(max_attempts):
        random_number = random.randint(10000000, 99999999)
        invoice_no = f"INV{random_number}"
        
        existing = db.query(Invoice).filter(
            Invoice.company_id == company_id,
            Invoice.invoice_no == invoice_no
        ).first()
        
        if not existing:
            return invoice_no
    
    raise Exception(f"Failed to generate unique invoice number after {max_attempts} attempts")

def number_to_words_indian(n):
    """Convert number to words in Indian format"""
    if n == 0:
        return 'Zero'
    
    ones = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine']
    teens = ['Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen']
    tens = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']
    
    def convert_hundreds(num):
        result = ''
        if num >= 100:
            result += ones[num // 100] + ' Hundred '
            num %= 100
        if num >= 20:
            result += tens[num // 10] + ' '
            num %= 10
        elif num >= 10:
            result += teens[num - 10] + ' '
            return result
        if num > 0:
            result += ones[num] + ' '
        return result
    
    if n < 0:
        return 'Minus ' + number_to_words_indian(abs(n))
    
    crore = n // 10000000
    n %= 10000000
    lakh = n // 100000
    n %= 100000
    thousand = n // 1000
    n %= 1000
    
    result = ''
    if crore > 0:
        result += convert_hundreds(crore) + 'Crore '
    if lakh > 0:
        result += convert_hundreds(lakh) + 'Lakh '
    if thousand > 0:
        result += convert_hundreds(thousand) + 'Thousand '
    if n > 0:
        result += convert_hundreds(n)
    
    return result.strip()


def generate_invoice_pdf(invoice_data: dict, company_data: dict, customer_data: dict, previous_invoices: list = None) -> bytes:
    """Generate invoice PDF matching the exact template provided by user"""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from io import BytesIO
    from datetime import datetime, timedelta
    
    try:
        pdfmetrics.registerFont(TTFont('DejaVu', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
        pdfmetrics.registerFont(TTFont('DejaVu-Bold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
        FONT_REGULAR = 'DejaVu'
        FONT_BOLD = 'DejaVu-Bold'
    except:
        FONT_REGULAR = 'Helvetica'
        FONT_BOLD = 'Helvetica-Bold'
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=10*mm, bottomMargin=10*mm, 
                           leftMargin=15*mm, rightMargin=15*mm)
    
    story = []
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=16, 
                                 textColor=colors.black, alignment=TA_CENTER,
                                 fontName=FONT_BOLD, spaceAfter=3*mm)
    
    company_style = ParagraphStyle('Company', parent=styles['Normal'], fontSize=11, 
                                   fontName=FONT_BOLD, alignment=TA_LEFT, leading=14)
    
    company_detail_style = ParagraphStyle('CompanyDetail', parent=styles['Normal'], fontSize=9, 
                                          fontName=FONT_REGULAR, alignment=TA_LEFT, leading=11)
    
    normal_style = ParagraphStyle('Normal', parent=styles['Normal'], fontSize=8, fontName=FONT_REGULAR, leading=10)
    bold_style = ParagraphStyle('Bold', parent=styles['Normal'], fontSize=8, fontName=FONT_BOLD, leading=10)
    small_style = ParagraphStyle('Small', parent=styles['Normal'], fontSize=7, fontName=FONT_REGULAR, leading=9)
    
    story.append(Paragraph("INVOICE", title_style))
    story.append(Spacer(1, 2*mm))
    
    issue_date_formatted = format_date_ddmmyyyy(invoice_data['issue_date'])
    
    prev_due_total = invoice_data.get('prev_due_total', 0)
    grand_total = invoice_data['total_amount'] + prev_due_total
    payment_received = invoice_data.get('payment_received', 0)
    amount_due_header = max(0, grand_total - payment_received)
    
    company_name = company_data.get('company_name', 'FIBERNET').upper()
    company_address = company_data.get('company_address', '')
    company_phone = company_data.get('company_phone', '')
    company_email = company_data.get('company_email', '')
    
    gst_needed = str(customer_data.get('gst_invoice_needed', '')).strip().lower() in ('yes', 'true', '1', 'on')
    
    company_text = f"<b>{company_name}</b><br/>" \
                   f"{company_address}<br/>" \
                   f"Mobile NO-{company_phone}<br/>" \
                   f"E-Mail : {company_email}"
    
    if gst_needed:
        company_state_raw = company_data.get('state', '')
        company_gst = company_data.get('gst_number', '')
        if company_state_raw:
            state_full_name, state_code = get_state_info(company_state_raw)
            company_text += f"<br/>State: {state_full_name}"
            if state_code:
                company_text += f"<br/>State Code: {state_code}"
        if company_gst:
            company_text += f"<br/>GSTIN: {company_gst}"
    
    invoice_details_data = [
        [Paragraph(f"<b>Invoice No:</b> {invoice_data['invoice_no']}", bold_style)],
        [Paragraph(f"<b>Dated:</b> {issue_date_formatted}", bold_style)],
        [Paragraph(f"<b>Total Due Amount:</b> ■ {int(amount_due_header)}", bold_style)],
        [Paragraph(f"<b>Due Date:</b> Immediately", bold_style)]
    ]
    
    invoice_details_table = Table(invoice_details_data, colWidths=[70*mm])
    invoice_details_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 2*mm),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2*mm),
        ('TOPPADDING', (0, 0), (-1, -1), 1.5*mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5*mm),
        ('LINEBELOW', (0, 0), (0, 2), 0.5, colors.black),
    ]))
    
    header_data = [
        [Paragraph(company_text, company_detail_style), invoice_details_table]
    ]
    
    header_table = Table(header_data, colWidths=[110*mm, 70*mm])
    header_table.setStyle(TableStyle([
        ('BOX', (0, 0), (0, 0), 0.5, colors.black),
        ('BOX', (1, 0), (1, 0), 0.5, colors.black),
        ('VALIGN', (0, 0), (0, 0), 'TOP'),
        ('VALIGN', (1, 0), (1, 0), 'TOP'),
        ('ALIGN', (0, 0), (0, 0), 'LEFT'),
        ('ALIGN', (1, 0), (1, 0), 'CENTER'),
        ('LEFTPADDING', (0, 0), (0, 0), 2*mm),
        ('RIGHTPADDING', (0, 0), (0, 0), 2*mm),
        ('TOPPADDING', (0, 0), (0, 0), 2*mm),
        ('BOTTOMPADDING', (0, 0), (0, 0), 2*mm),
        ('LEFTPADDING', (1, 0), (1, 0), 0),
        ('RIGHTPADDING', (1, 0), (1, 0), 0),
        ('TOPPADDING', (1, 0), (1, 0), 0),
        ('BOTTOMPADDING', (1, 0), (1, 0), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 3*mm))
    
    billing_type = customer_data.get('billing_type', 'PREPAID').upper()
    category = customer_data.get('category', 'Broadband')
    
    buyer_text = f"<b>Buyer</b><br/>" \
                 f"{customer_data.get('customer_name', '')} ({customer_data.get('mobile', '')})<br/>" \
                 f"Username: {customer_data.get('username', '')}<br/>" \
                 f"Billing Type: {billing_type}<br/>" \
                 f"Category: {category}<br/>"
    
    if gst_needed:
        customer_state_raw = customer_data.get('state', '')
        customer_gst = customer_data.get('customer_gst_no', '')
        if customer_state_raw:
            state_full_name, state_code = get_state_info(customer_state_raw)
            buyer_text += f"State: {state_full_name}<br/>"
            if state_code:
                buyer_text += f"State Code: {state_code}<br/>"
        if customer_gst:
            buyer_text += f"GSTIN: {customer_gst}<br/>"
    
    buyer_text += f"BILLING ADDRESS:{customer_data.get('address', '')}<br/>" \
                  f"{customer_data.get('mobile', '')}"
    
    buyer_data = [[Paragraph(buyer_text, normal_style)]]
    buyer_table = Table(buyer_data, colWidths=[180*mm])
    buyer_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2*mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2*mm),
        ('LEFTPADDING', (0, 0), (-1, -1), 2*mm),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2*mm),
    ]))
    story.append(buyer_table)
    story.append(Spacer(1, 2*mm))
    
    from dateutil.relativedelta import relativedelta
    
    invoice_type = invoice_data.get('invoice_type', 'regular')
    is_addon = (invoice_type == 'addon')
    
    if not is_addon:
        period_months = invoice_data.get('period_months', 1)
        start_date_str = invoice_data.get('start_date', '')
        end_date_str = invoice_data.get('end_date', '')
        
        if start_date_str and end_date_str:
            if billing_type == 'POSTPAID':
                try:
                    start_date_dt = datetime.strptime(start_date_str, '%Y-%m-%d')
                    display_start_dt = start_date_dt - relativedelta(months=period_months)
                    display_end_dt = start_date_dt - timedelta(days=1)
                    start_date_formatted = display_start_dt.strftime('%d-%m-%Y')
                    end_date_formatted = display_end_dt.strftime('%d-%m-%Y')
                except:
                    start_date_formatted = format_date_ddmmyyyy(start_date_str)
                    end_date_formatted = format_date_ddmmyyyy(end_date_str)
            else:
                start_date_formatted = format_date_ddmmyyyy(start_date_str)
                try:
                    end_date_dt = datetime.strptime(end_date_str, '%Y-%m-%d')
                    display_end_date = end_date_dt - timedelta(days=1)
                    end_date_formatted = display_end_date.strftime('%d-%m-%Y')
                except:
                    end_date_formatted = format_date_ddmmyyyy(end_date_str)
            
            period_str = f"PERIOD {start_date_formatted} TO {end_date_formatted}"
        else:
            period_str = ""
    else:
        period_str = ""
    
    plan_name = invoice_data.get('plan_name', 'Broadband').upper()
    base_amount = int(invoice_data['base_amount'])
    
    line_items = invoice_data.get('line_items', [])
    payment_received = invoice_data.get('payment_received', 0)
    is_first_invoice = invoice_data.get('is_first_invoice', False)
    is_postpaid = invoice_data.get('is_postpaid', False)
    
    items_data = [
        [Paragraph("<b>S.NO.</b>", bold_style), 
         Paragraph("<b>Description of Goods</b>", bold_style),
         Paragraph("<b>HSN/SAC</b>", bold_style),
         Paragraph("<b>Quantity</b>", bold_style),
         Paragraph("<b>Rate Per</b>", bold_style),
         Paragraph("<b>Disc. %</b>", bold_style),
         Paragraph("<b>Amount</b>", bold_style)]
    ]
    
    if is_addon:
        addon_description = invoice_data.get('description', 'Manual/Addon Charges')
        items_data.append([
            Paragraph("1", normal_style),
            Paragraph(addon_description, normal_style),
            Paragraph("998422", normal_style),
            Paragraph("1 nos", normal_style),
            Paragraph(str(base_amount), normal_style),
            Paragraph("0", normal_style),
            Paragraph(str(base_amount), normal_style)
        ])
    # Always use line_items when available to show all charges (plan, installation, security, discount)
    elif line_items:
        for idx, item in enumerate(line_items, start=1):
            description = item['description']
            if idx == 1 and billing_type == 'PREPAID' and not is_postpaid and period_str:
                description = f"{description}<br/>{period_str}"
            
            items_data.append([
                Paragraph(str(idx), normal_style),
                Paragraph(description, normal_style),
                Paragraph(item['hsn_sac'], normal_style),
                Paragraph(item['quantity'], normal_style),
                Paragraph(str(item['rate']), normal_style),
                Paragraph("0", normal_style),
                Paragraph(str(item['amount']), normal_style)
            ])
    else:
        total_amount_for_period = int(invoice_data.get('total_amount', base_amount))
        description_text = f"{plan_name}"
        if period_str:
            description_text += f"<br/>{period_str}"
        
        items_data.append([
            Paragraph("1", normal_style),
            Paragraph(description_text, normal_style),
            Paragraph("998422", normal_style),
            Paragraph("1 nos", normal_style),
            Paragraph(str(total_amount_for_period), normal_style),
            Paragraph("0", normal_style),
            Paragraph(str(total_amount_for_period), normal_style)
        ])
    
    if previous_invoices and len(previous_invoices) > 0:
        invoice_details = []
        for inv in previous_invoices[:5]:  # Limit to first 5 invoices to avoid overflow
            inv_no = inv.get('invoice_no', '')
            inv_amt = inv.get('amount', 0)
            invoice_details.append(f"{inv_no} (₹{inv_amt:,.0f})")
        
        if len(previous_invoices) > 5:
            invoice_details.append(f"+{len(previous_invoices) - 5} more")
        
        invoice_desc = '<br/>'.join(invoice_details)
        prev_total = round(prev_due_total)
        items_data.append([
            Paragraph("2", normal_style),
            Paragraph(f"Previous Balance<br/>{invoice_desc}", normal_style),
            Paragraph("998422", normal_style),
            Paragraph("1 nos", normal_style),
            Paragraph(str(prev_total), normal_style),
            Paragraph("0", normal_style),
            Paragraph(str(prev_total), normal_style)
        ])
    
    # Add payment deduction row if payment was received
    if payment_received > 0:
        items_data.append([
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            Paragraph("<b>Subtotal</b>", bold_style),
            Paragraph(f"<b>{int(grand_total)}</b>", bold_style)
        ])
        items_data.append([
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            Paragraph("<b>Less: Payment Received</b>", bold_style),
            Paragraph(f"<b>-{int(payment_received)}</b>", bold_style)
        ])
        amount_due = grand_total - payment_received
        items_data.append([
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            Paragraph("<b>Amount Due</b>", bold_style),
            Paragraph(f"<b>{int(amount_due)}</b>", bold_style)
        ])
    else:
        items_data.append([
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            Paragraph("<b>Total</b>", bold_style),
            Paragraph(f"<b>{int(grand_total)}</b>", bold_style)
        ])
    
    items_table = Table(items_data, colWidths=[12*mm, 70*mm, 18*mm, 18*mm, 18*mm, 15*mm, 29*mm])
    items_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
        ('GRID', (0, 0), (-1, -2), 0.25, colors.grey),
        ('LINEABOVE', (0, -1), (-1, -1), 0.5, colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 1), (1, -1), 'LEFT'),
        ('ALIGN', (5, -1), (6, -1), 'RIGHT'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 1.5*mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5*mm),
        ('LEFTPADDING', (0, 0), (-1, -1), 1*mm),
        ('RIGHTPADDING', (0, 0), (-1, -1), 1*mm),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 2*mm))
    
    amount_words = number_to_words_indian(int(grand_total))
    amount_words_text = f"<b>Amount Chargeable (in words) E. & O.E</b><br/>{amount_words} ONLY"
    
    amount_words_data = [[Paragraph(amount_words_text, normal_style)]]
    amount_words_table = Table(amount_words_data, colWidths=[180*mm])
    amount_words_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
        ('TOPPADDING', (0, 0), (-1, -1), 1.5*mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5*mm),
        ('LEFTPADDING', (0, 0), (-1, -1), 2*mm),
    ]))
    story.append(amount_words_table)
    story.append(Spacer(1, 2*mm))
    
    cgst_tax = invoice_data.get('cgst_tax', 0)
    sgst_tax = invoice_data.get('sgst_tax', 0)
    igst_tax = invoice_data.get('igst_tax', 0)
    
    cgst_rate = invoice_data.get('cgst_percent', (cgst_tax / base_amount * 100) if base_amount > 0 else 0)
    sgst_rate = invoice_data.get('sgst_percent', (sgst_tax / base_amount * 100) if base_amount > 0 else 0)
    igst_rate = invoice_data.get('igst_percent', (igst_tax / base_amount * 100) if base_amount > 0 else 0)
    total_tax = cgst_tax + sgst_tax + igst_tax
    
    tax_header = [
        Paragraph("", bold_style),
        Paragraph("", bold_style),
        Paragraph("<b>IGST Tax</b>", bold_style),
        Paragraph("", bold_style),
        Paragraph("<b>CGST Tax</b>", bold_style),
        Paragraph("", bold_style),
        Paragraph("<b>SGST Tax</b>", bold_style),
        Paragraph("", bold_style),
        Paragraph("<b>Total Tax</b>", bold_style)
    ]
    
    tax_subheader = [
        Paragraph("<b>HSN/SAC</b>", bold_style),
        Paragraph("<b>Taxable Value</b>", bold_style),
        Paragraph("<b>Rate</b>", bold_style),
        Paragraph("<b>Amount</b>", bold_style),
        Paragraph("<b>Rate</b>", bold_style),
        Paragraph("<b>Amount</b>", bold_style),
        Paragraph("<b>Rate</b>", bold_style),
        Paragraph("<b>Amount</b>", bold_style),
        Paragraph("<b>Total Tax<br/>Amount</b>", bold_style)
    ]
    
    tax_values = [
        Paragraph("998422", normal_style),
        Paragraph(str(base_amount), normal_style),
        Paragraph(f"{igst_rate:.1f}%", normal_style),
        Paragraph(str(int(igst_tax)), normal_style),
        Paragraph(f"{cgst_rate:.1f}%", normal_style),
        Paragraph(str(int(cgst_tax)), normal_style),
        Paragraph(f"{sgst_rate:.1f}%", normal_style),
        Paragraph(str(int(sgst_tax)), normal_style),
        Paragraph(str(int(total_tax)), normal_style)
    ]
    
    tax_data = [tax_header, tax_subheader, tax_values]
    
    tax_table = Table(tax_data, colWidths=[18*mm, 25*mm, 15*mm, 18*mm, 15*mm, 18*mm, 15*mm, 18*mm, 38*mm])
    tax_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('SPAN', (2, 0), (3, 0)),
        ('SPAN', (4, 0), (5, 0)),
        ('SPAN', (6, 0), (7, 0)),
        ('SPAN', (8, 0), (8, 1)),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('TOPPADDING', (0, 0), (-1, -1), 1*mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1*mm),
        ('LEFTPADDING', (0, 0), (-1, -1), 0.5*mm),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0.5*mm),
    ]))
    story.append(tax_table)
    story.append(Spacer(1, 2*mm))
    
    tax_words = number_to_words_indian(int(total_tax))
    tax_words_text = f"<b>Tax Amount (in words) :</b> {tax_words}"
    balance_text = f"<b>Balance :</b> {int(grand_total)}"
    
    tax_balance_data = [[Paragraph(tax_words_text + "<br/>" + balance_text, normal_style)]]
    tax_balance_table = Table(tax_balance_data, colWidths=[180*mm])
    tax_balance_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
        ('TOPPADDING', (0, 0), (-1, -1), 1.5*mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5*mm),
        ('LEFTPADDING', (0, 0), (-1, -1), 2*mm),
    ]))
    story.append(tax_balance_table)
    story.append(Spacer(1, 2*mm))
    
    declaration_text = company_data.get('declaration', 'Thanks for your business. Hope you are enjoying our services.')
    terms_text = company_data.get('terms_conditions', 
                                   '1. Kindly Pay Your Total Due Amount Before/Till Due Date to Avoid Late Payment Charges.\n'
                                   '2. Cheque Bounce Penalty will be 500 Rupees Per Cheque.\n'
                                   '3. No Refund will be made after Payment Submission.\n'
                                   '4. No Refund will be made if User Paid Annual subscription.')
    
    declaration_content = f"<b>Declaration</b><br/>{declaration_text}<br/><br/>" \
                         f"<b>Terms & Conditions:</b>{terms_text}"
    
    declaration_data = [[Paragraph(declaration_content, small_style)]]
    declaration_table = Table(declaration_data, colWidths=[180*mm])
    declaration_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
        ('TOPPADDING', (0, 0), (-1, -1), 2*mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2*mm),
        ('LEFTPADDING', (0, 0), (-1, -1), 2*mm),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2*mm),
    ]))
    story.append(declaration_table)
    story.append(Spacer(1, 2*mm))
    
    bank_name = company_data.get('bank_name', 'BANK OF BARODA')
    account_number = company_data.get('account_number', '')
    branch_ifsc = company_data.get('branch_ifsc', '')
    
    bank_details_text = f"<b>Company Bank Details</b><br/>" \
                       f"Bank Name: {bank_name}<br/>" \
                       f"A/c No. {account_number}<br/>" \
                       f"Branch & IFS Code: {branch_ifsc}"
    
    signature_text = f"for {company_name}<br/><br/><br/><br/>Authorised Signatory"
    
    footer_data = [
        [Paragraph(bank_details_text, normal_style),
         Paragraph(signature_text, normal_style)]
    ]
    
    footer_table = Table(footer_data, colWidths=[90*mm, 90*mm])
    footer_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
        ('LINEAFTER', (0, 0), (0, 0), 0.5, colors.black),
        ('VALIGN', (0, 0), (0, 0), 'TOP'),
        ('VALIGN', (1, 0), (1, 0), 'BOTTOM'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -1), 2*mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2*mm),
        ('LEFTPADDING', (0, 0), (-1, -1), 2*mm),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2*mm),
    ]))
    story.append(footer_table)
    
    doc.build(story)
    buffer.seek(0)
    return buffer.read()


def generate_caf_pdf(customer_data: dict, company_data: dict) -> bytes:
    """Generate CAF (Customer Application Form) PDF matching the exact format with company branding"""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm, mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, KeepTogether
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from io import BytesIO
    import os
    
    try:
        pdfmetrics.registerFont(TTFont('DejaVu', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
        pdfmetrics.registerFont(TTFont('DejaVu-Bold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
        FONT_REGULAR = 'DejaVu'
        FONT_BOLD = 'DejaVu-Bold'
    except:
        FONT_REGULAR = 'Helvetica'
        FONT_BOLD = 'Helvetica-Bold'
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=1.5*cm, bottomMargin=1.5*cm,
                           leftMargin=2*cm, rightMargin=2*cm)
    
    story = []
    styles = getSampleStyleSheet()
    
    company_name_style = ParagraphStyle('CompanyName', parent=styles['Normal'], fontSize=14,
                                       fontName=FONT_BOLD, alignment=TA_CENTER, spaceAfter=2)
    company_address_style = ParagraphStyle('CompanyAddress', parent=styles['Normal'], fontSize=9,
                                          fontName=FONT_REGULAR, alignment=TA_CENTER, spaceAfter=8)
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=12,
                                fontName=FONT_BOLD, alignment=TA_CENTER, spaceAfter=10)
    section_header_style = ParagraphStyle('SectionHeader', parent=styles['Normal'], fontSize=10,
                                         fontName=FONT_BOLD, spaceAfter=4)
    label_style = ParagraphStyle('Label', parent=styles['Normal'], fontSize=9, fontName=FONT_REGULAR)
    value_style = ParagraphStyle('Value', parent=styles['Normal'], fontSize=9, fontName=FONT_REGULAR)
    small_style = ParagraphStyle('Small', parent=styles['Normal'], fontSize=8, fontName=FONT_REGULAR)
    tiny_style = ParagraphStyle('Tiny', parent=styles['Normal'], fontSize=7, fontName=FONT_REGULAR, alignment=TA_CENTER)
    
    company_name = company_data.get('company_name', 'AUTO ISP BILLING').upper()
    company_address = company_data.get('company_address', '')
    company_city = company_data.get('city', '')
    company_state = company_data.get('state', '')
    
    logo_img = None
    logo_path = company_data.get('logo_path', '')
    if logo_path:
        if logo_path.startswith('/static/'):
            logo_path = os.path.join('/home/ubuntu/autoispbilling-payfast-repo', logo_path.lstrip('/'))
        if os.path.exists(logo_path):
            try:
                logo_img = Image(logo_path, width=5*cm, height=1.8*cm, kind='proportional')
            except:
                pass
    
    photo_box_data = [[Paragraph("Paste<br/>Passport Size<br/>Photograph", tiny_style)]]
    photo_box = Table(photo_box_data, colWidths=[3.5*cm], rowHeights=[4.5*cm])
    photo_box.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    
    center_elements = []
    if logo_img:
        center_elements.append(logo_img)
    center_elements.append(Paragraph(company_name, company_name_style))
    address_text = f"{company_address}"
    if company_city or company_state:
        address_text += f"<br/>{company_city}, {company_state}"
    center_elements.append(Paragraph(address_text, company_address_style))
    
    center_table = Table([[elem] for elem in center_elements], colWidths=[8*cm])
    center_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    
    caf_data = [[Paragraph(f"<b>CAF No.</b>", label_style)], 
                [Paragraph(customer_data.get('caf_no', ''), value_style)]]
    caf_box = Table(caf_data, colWidths=[4*cm])
    caf_box.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 3),
    ]))
    
    header_row = Table([[photo_box, center_table, caf_box]], colWidths=[4.5*cm, 8*cm, 4.5*cm])
    header_row.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (0, 0), (0, 0), 'LEFT'),
        ('ALIGN', (1, 0), (1, 0), 'CENTER'),
        ('ALIGN', (2, 0), (2, 0), 'RIGHT'),
    ]))
    story.append(header_row)
    story.append(Spacer(1, 5*mm))
    
    story.append(Paragraph("<b>Customer Application Form</b>", title_style))
    story.append(Spacer(1, 5*mm))
    
    story.append(Paragraph("<b>Personal / Company Details</b>", section_header_style))
    personal_data = [
        [Paragraph("<b>Customer ID :</b>", label_style), Paragraph(customer_data.get('customer_id', ''), value_style),
         Paragraph("<b>User Name :</b>", label_style), Paragraph(customer_data.get('username', ''), value_style)],
        [Paragraph("<b>Name of the Customer :</b>", label_style), Paragraph(customer_data.get('customer_name', ''), value_style),
         Paragraph("<b>S/o, D/o, W/o :</b>", label_style), Paragraph('', value_style)],
        [Paragraph("<b>Date of Birth :</b>", label_style), Paragraph('', value_style),
         Paragraph("<b>Gender :</b>", label_style), Paragraph('', value_style)],
        [Paragraph("<b>Nationality :</b>", label_style), Paragraph('', value_style), '', ''],
    ]
    personal_table = Table(personal_data, colWidths=[4*cm, 4.5*cm, 3.5*cm, 5*cm])
    personal_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(personal_table)
    story.append(Spacer(1, 5*mm))
    
    story.append(Paragraph("<b>Customer Address</b>", section_header_style))
    address_data = [
        [Paragraph("<b>Billing Address :</b>", label_style), Paragraph('', value_style),
         Paragraph("<b>City :</b>", label_style), Paragraph('', value_style)],
        [Paragraph("<b>Pin Code :</b>", label_style), Paragraph('', value_style),
         Paragraph("<b>State :</b>", label_style), Paragraph('', value_style)],
        [Paragraph("<b>Country :</b>", label_style), Paragraph('India', value_style), '', ''],
        [Paragraph("<b>Installation Address :</b>", label_style), Paragraph(customer_data.get('address', ''), value_style),
         Paragraph("<b>City :</b>", label_style), Paragraph(customer_data.get('city', ''), value_style)],
        [Paragraph("<b>Pin Code :</b>", label_style), Paragraph(customer_data.get('pincode', ''), value_style),
         Paragraph("<b>State :</b>", label_style), Paragraph(customer_data.get('state', ''), value_style)],
        [Paragraph("<b>Country :</b>", label_style), Paragraph('India', value_style),
         Paragraph("<b>Areacode :</b>", label_style), Paragraph(customer_data.get('locality', ''), value_style)],
    ]
    address_table = Table(address_data, colWidths=[4*cm, 4.5*cm, 3.5*cm, 5*cm])
    address_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(address_table)
    story.append(Spacer(1, 5*mm))
    
    story.append(Paragraph("<b>Document Proof (Attach photocopies of address proof and Photo ID)</b>", section_header_style))
    doc_data = [
        [Paragraph("<b>Address Proof :</b>", label_style), Paragraph(customer_data.get('id_proof', ''), value_style),
         Paragraph("<b>Address Proof ID No. :</b>", label_style), Paragraph(customer_data.get('id_proof_no', ''), value_style)],
        [Paragraph("<b>Photo ID Proof :</b>", label_style), Paragraph(customer_data.get('id_proof', ''), value_style),
         Paragraph("<b>Photo ID No : :</b>", label_style), Paragraph(customer_data.get('id_proof_no', ''), value_style)],
    ]
    doc_table = Table(doc_data, colWidths=[4*cm, 4.5*cm, 4.5*cm, 4*cm])
    doc_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(doc_table)
    story.append(Spacer(1, 5*mm))
    
    story.append(Paragraph("<b>Connection Details</b>", section_header_style))
    
    monthly_amount = float(customer_data.get('monthly_amount', 0))
    period = int(customer_data.get('period', 1))
    total_amount = monthly_amount * period
    security_deposit = float(customer_data.get('security_deposit', 0))
    installation_charges = float(customer_data.get('installation_charges', 0))
    customer_type = customer_data.get('customer_type', 'Prepaid').strip()
    
    cgst_tax = invoice_data.get('cgst_tax', 0)
    sgst_tax = invoice_data.get('sgst_tax', 0)
    igst_tax = invoice_data.get('igst_tax', 0)
    total_tax = cgst_tax + sgst_tax + igst_tax
    
    monthly_amount_with_tax = monthly_amount + total_tax
    
    if customer_type.lower() == 'postpaid':
        display_security = f"{round(monthly_amount_with_tax)}"
        display_plan_charges = ''
    else:
        display_security = f"{round(security_deposit)}"
        display_plan_charges = f"{round(monthly_amount_with_tax)}"
    
    connection_data = [
        [Paragraph("<b>Customer Type :</b>", label_style), Paragraph(customer_type, value_style), '', ''],
        [Paragraph("<b>Installation Amount :</b>", label_style), Paragraph(f"{round(installation_charges)}", value_style),
         Paragraph("<b>Security Deposite :</b>", label_style), Paragraph(display_security, value_style)],
        [Paragraph("<b>Plan Details :</b>", label_style), Paragraph(customer_data.get('plan_name', ''), value_style),
         Paragraph("<b>Plan Charges :</b>", label_style), Paragraph(display_plan_charges, value_style)],
        [Paragraph("<b>Bill Amount :</b>", label_style), Paragraph(f"{round(total_amount)}", value_style),
         Paragraph("<b>Connection Type :</b>", label_style), Paragraph(customer_data.get('service_type', 'Broadband'), value_style)],
        ['', Paragraph("<b>(GST inclusive)</b>", small_style), '', Paragraph("<b>(GST exclusive)</b>", small_style)],
        [Paragraph("<b>Set Top Box No. :</b>", label_style), Paragraph('', value_style),
         Paragraph("<b>VC No. :</b>", label_style), Paragraph('', value_style)],
        [Paragraph("<b>Modem No. :</b>", label_style), Paragraph(customer_data.get('modem_no', ''), value_style),
         Paragraph("<b>Modem No. Detail :</b>", label_style), Paragraph('', value_style)],
        [Paragraph("<b>MAC Address :</b>", label_style), Paragraph(customer_data.get('mac_address', ''), value_style),
         Paragraph("<b>MAC Address Detail :</b>", label_style), Paragraph('', value_style)],
        [Paragraph("<b>IP Addess :</b>", label_style), Paragraph(customer_data.get('ip_address', ''), value_style),
         Paragraph("<b>Vendor :</b>", label_style), Paragraph(customer_data.get('vendor', ''), value_style)],
        [Paragraph("<b>Under Scheme :</b>", label_style), Paragraph('', value_style), '', ''],
    ]
    connection_table = Table(connection_data, colWidths=[4*cm, 4.5*cm, 4*cm, 4.5*cm])
    connection_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(connection_table)
    story.append(Spacer(1, 5*mm))
    
    story.append(Paragraph("<b>Contact Details</b>", section_header_style))
    contact_data = [
        [Paragraph("<b>Email :</b>", label_style), Paragraph(customer_data.get('customer_email', ''), value_style),
         Paragraph("<b>Alternate Email :</b>", label_style), Paragraph('', value_style)],
        [Paragraph("<b>Mobile :</b>", label_style), Paragraph(customer_data.get('customer_phone', ''), value_style),
         Paragraph("<b>Alternate Mobile :</b>", label_style), Paragraph(customer_data.get('alt_mobile', ''), value_style)],
        [Paragraph("<b>Registration Date :</b>", label_style), Paragraph(customer_data.get('installation_date', ''), value_style),
         Paragraph("<b>Landline No. :</b>", label_style), Paragraph('', value_style)],
    ]
    contact_table = Table(contact_data, colWidths=[4*cm, 4.5*cm, 4*cm, 4.5*cm])
    contact_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(contact_table)
    story.append(Spacer(1, 8*mm))
    
    story.append(Paragraph("<b>DECLARATION</b>", section_header_style))
    declaration_text = f"""• I, hereby declare that I have applied for a new Broadband Internet connection with M/s {company_name}.<br/>
• I submit that my installation address is the same as mentioned above and the documentary proof issued by Govt. of India evidencing the proof of my permanent residence is duly submitted herewith.<br/>
• I hereby submit that I reside in the Installation Address mentioned in the Customer Application Form (CAF) and the Broadband Internet Services to be subscribed by me shall be used for my own personal use. I undertake to indemnify {company_name} against any claims or legal actions that may arise in case of any misuse or any act contrary to the terms & conditions mentioned under the CAF."""
    story.append(Paragraph(declaration_text, label_style))
    story.append(Spacer(1, 8*mm))
    
    signature_data = [
        [Paragraph("<b>Customer Signature</b><br/>Mobile ({0})<br/>OTP Verified: YES".format(customer_data.get('customer_phone', '')), label_style),
         Paragraph("<b>Date:</b> {0}<br/>{1}".format(customer_data.get('installation_date', ''), ''), label_style),
         Paragraph("<b>Authorized Signatory</b><br/>{0}".format(company_name), label_style)],
        [Paragraph("<b>Place:</b> {0}".format(company_city), label_style), '', ''],
    ]
    signature_table = Table(signature_data, colWidths=[5.5*cm, 5.5*cm, 6*cm])
    signature_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(signature_table)
    story.append(Spacer(1, 8*mm))
    
    story.append(Paragraph("<b>Terms & Conditions</b>", section_header_style))
    terms_text = f"""<b>1. About</b><br/>
This Agreement for subscription of Broadband Internet and other value added services (Hereinafter referred to as 'Services') is entered between {company_name}, a Company incorporated under Companies act, 1956 having its registered office at {company_address}. {company_name} is licensed Internet Service Provider holding valid license issued by the Department of Telecommunications (DOT), Govt. of India. Any individual/ entity/legal person subscribing to the services offered by {company_name} are hereunder referred to as the 'subscriber'.<br/><br/>
<b>2. Service</b><br/>
{company_name} provides its services via Fiber optic cables, which requires us to install and power CX (Customer Switch) at the installation Address provided by me herein above. I accept this requirement and hereby accord the permission for installing this CX and give power for the same if required by {company_name}, so that {company_name} internet services may be installed and commissioned. The subscriber is also responsible to provide all access to equipment necessary to access the services. All the subsequent services manuals/packages/booklets etc. issued by {company_name} from time to time shall be binding on Subscriber. {company_name} reserves the right to modify and amend these terms and conditions in part or full and the amended one, as notified by {company_name} in its website, shall be binding on the subscriber. The Subscriber shall provide valid proof of address and proof of identity as per the direction issued by DOT from time to time to subscribe the {company_name} services and as and when required by {company_name}.<br/><br/>
<b>3. Customer Premises Network Equipment (CPNE)</b><br/>
The Subscriber acknowledges that Last mile switch namely Optical Network Terminal, Router, Wifi Routers and such other network connectivity equipments ("Customer Premises Network Equipment/CPNE") installed at the customer premises is a network equipment of {company_name} used for the purposes of providing Broadband Internet services to the Subscriber. The Subscriber further agrees that the CPNE installed at his / her premises are not part of the Service package and are highly capital intensive in nature.<br/>
The CPNE shall always remain the sole and exclusive property of {company_name} and the subscriber shall not handle or tamper with the same. In case of discontinuation or termination of Services due to any reasons whatsoever, the Subscriber shall duly return the CPNE to {company_name} in a reasonable and proper working condition to the satisfaction {company_name}. {company_name} may require the subscriber to pay installation charges as prescribed by {company_name} from time to time towards installation of such Customer Premises Network Equipment. In case of Subscriber duly returning the CPNE in proper working condition to the satisfaction of {company_name}, {company_name} may choose to refund the said installation charges either fully or partially, if any collected by {company_name}, at its sole discretion as an incentive to the subscriber at the time of disconnection or termination of the Services. The decision of {company_name} in this regard shall be final and binding on the subscriber. The subscriber recognizes that {company_name} is merely the supplier of CPNE (or any other hardware that be supplied), {company_name} makes no warranties of any kind, expressed or implied in respect of the same. Warranties in respect of all hardware supplied by {company_name} will be made and issued by the respective manufacturer."""
    story.append(Paragraph(terms_text, small_style))
    
    doc.build(story)
    pdf_data = buffer.getvalue()
    buffer.close()
    
    return pdf_data
async def send_invoice_email(invoice_data: dict, customer_email: str, company_data: dict, pdf_data: bytes, billing_type: str = 'PREPAID'):
    """Send invoice via email as background task with QR code"""
    import smtplib
    import os
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.application import MIMEApplication
    from email.mime.image import MIMEImage
    from datetime import datetime, timedelta
    
    try:
        smtp_server = company_data.get('smtp_server')
        smtp_port = company_data.get('smtp_port')
        smtp_username = company_data.get('smtp_username')
        smtp_password = company_data.get('smtp_password')
        
        if not all([smtp_server, smtp_port, smtp_username, smtp_password]):
            print(f"✗ SMTP not configured for invoice {invoice_data.get('invoice_no', 'UNKNOWN')}: smtp_server={bool(smtp_server)}, smtp_port={smtp_port}, smtp_username={bool(smtp_username)}, smtp_password={bool(smtp_password)}")
            return {"success": False, "message": "SMTP not configured. Please configure email settings in company profile."}
        
        msg = MIMEMultipart('related')
        msg['From'] = company_data.get('smtp_username')
        msg['To'] = customer_email
        msg['Subject'] = f"Invoice - {invoice_data['invoice_no']}"
        
        msg_alternative = MIMEMultipart('alternative')
        
        issue_date_str = invoice_data.get('issue_date', '')
        start_date_str = invoice_data.get('start_date', '')
        end_date_str = invoice_data.get('end_date', '')
        
        try:
            issue_date_formatted = datetime.strptime(issue_date_str, '%Y-%m-%d').strftime('%d-%m-%Y') if issue_date_str else ''
        except:
            issue_date_formatted = issue_date_str or ''
        
        try:
            start_date_formatted = datetime.strptime(start_date_str, '%Y-%m-%d').strftime('%d-%m-%Y') if start_date_str else ''
        except:
            start_date_formatted = start_date_str or ''
        
        try:
            if end_date_str:
                end_date_obj = datetime.strptime(end_date_str, '%Y-%m-%d')
                if billing_type.upper() == 'PREPAID':
                    end_date_display = end_date_obj - timedelta(days=1)
                else:
                    end_date_display = end_date_obj
                end_date_formatted = end_date_display.strftime('%d-%m-%Y')
            else:
                end_date_formatted = ''
        except:
            end_date_formatted = end_date_str or ''
        
        current_amount = round(invoice_data['total_amount'])
        prev_due = round(invoice_data.get('prev_due_total', 0))
        grand_total = round(current_amount + prev_due)
        payment_received = round(invoice_data.get('payment_received', 0))
        amount_due = round(max(0, grand_total - payment_received))
        
        invoice_type = invoice_data.get('invoice_type', 'regular')
        
        if invoice_type == 'addon':
            text_body = f"""Dear {invoice_data.get('customer_name', 'Customer')},

Thank you for your Purchase from {company_data.get('company_name', 'Auto ISP Billing')}. Please find your attached invoice.

Invoice Details:
- Invoice Number: {invoice_data['invoice_no']}
- Issue Date: {issue_date_formatted}
- Total Amount Due: ₹{amount_due}

Scan QR code to pay (if available).

If you have any questions, please contact us.

Best regards,
{company_data.get('company_name', 'Auto ISP Billing')}
"""
            html_body = f"""<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
    <p>Dear {invoice_data.get('customer_name', 'Customer')},</p>
    
    <p>Thank you for your Purchase from <strong>{company_data.get('company_name', 'Auto ISP Billing')}</strong>. Please find your attached invoice.</p>
    
    <div style="background: #f9f9f9; padding: 15px; border-left: 4px solid #4A9B9B; margin: 20px 0;">
        <h3 style="margin-top: 0; color: #4A9B9B;">Invoice Details:</h3>
        <p style="margin: 5px 0;"><strong>Invoice Number:</strong> {invoice_data['invoice_no']}</p>
        <p style="margin: 5px 0;"><strong>Issue Date:</strong> {issue_date_formatted}</p>
        <p style="margin: 5px 0; font-size: 18px; color: #d9534f;"><strong>Total Amount Due: ₹{amount_due}</strong></p>
    </div>
    
    <div style="text-align: center; margin: 20px 0;">
        <p style="font-weight: bold; margin-bottom: 10px;">Scan to Pay:</p>
        <img src="cid:payment_qr" alt="Payment QR Code" style="max-width: 200px; height: auto; border: 2px solid #ddd; padding: 10px;">
    </div>
    
    <p>If you have any questions, please contact us.</p>
    
    <p style="margin-top: 20px;">
        Best regards,<br>
        <strong>{company_data.get('company_name', 'Auto ISP Billing')}</strong>
    </p>
</body>
</html>"""
        else:
            text_body = f"""Dear {invoice_data.get('customer_name', 'Customer')},

Thank you for your subscription renewal. Please find attached your invoice.

Invoice Details:
- Invoice Number: {invoice_data['invoice_no']}
- Issue Date: {issue_date_formatted}
- Total Amount Due: ₹{amount_due}

Scan QR code to pay (if available).

If you have any questions, please contact us.

Best regards,
{company_data.get('company_name', 'Auto ISP Billing')}
"""
            html_body = f"""<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
    <p>Dear {invoice_data.get('customer_name', 'Customer')},</p>
    
    <p>Thank you for your subscription renewal. Please find attached your invoice.</p>
    
    <div style="background: #f9f9f9; padding: 15px; border-left: 4px solid #4A9B9B; margin: 20px 0;">
        <h3 style="margin-top: 0; color: #4A9B9B;">Invoice Details:</h3>
        <p style="margin: 5px 0;"><strong>Invoice Number:</strong> {invoice_data['invoice_no']}</p>
        <p style="margin: 5px 0;"><strong>Issue Date:</strong> {issue_date_formatted}</p>
        <p style="margin: 5px 0; font-size: 18px; color: #d9534f;"><strong>Total Amount Due: ₹{amount_due}</strong></p>
    </div>
    
    <div style="text-align: center; margin: 20px 0;">
        <p style="font-weight: bold; margin-bottom: 10px;">Scan to Pay:</p>
        <img src="cid:payment_qr" alt="Payment QR Code" style="max-width: 200px; height: auto; border: 2px solid #ddd; padding: 10px;">
    </div>
    
    <p>If you have any questions, please contact us.</p>
    
    <p style="margin-top: 20px;">
        Best regards,<br>
        <strong>{company_data.get('company_name', 'Auto ISP Billing')}</strong>
    </p>
</body>
</html>"""
        
        qr_path = company_data.get('bank_qr_code', None)
        if qr_path and qr_path.startswith('/static/'):
            qr_path = qr_path.lstrip('/')
        has_qr = bool(qr_path and os.path.exists(qr_path))
        
        # Only include QR section in HTML if QR code exists
        if has_qr:
            html_body_with_qr = html_body  # HTML already includes QR section
        else:
            html_body_with_qr = html_body.replace(
                '''    <div style="text-align: center; margin: 20px 0;">
        <p style="font-weight: bold; margin-bottom: 10px;">Scan to Pay:</p>
        <img src="cid:payment_qr" alt="Payment QR Code" style="max-width: 200px; height: auto; border: 2px solid #ddd; padding: 10px;">
    </div>
    ''', '')
            text_body = text_body.replace('\nScan QR code to pay (if available).\n', '\n')
        
        msg_alternative.attach(MIMEText(text_body, 'plain'))
        msg_alternative.attach(MIMEText(html_body_with_qr, 'html'))
        msg.attach(msg_alternative)
        
        pdf_attachment = MIMEApplication(pdf_data, _subtype='pdf')
        pdf_attachment.add_header('Content-Disposition', 'attachment', filename=f'invoice_{invoice_data["invoice_no"]}.pdf')
        msg.attach(pdf_attachment)
        
        if has_qr:
            try:
                with open(qr_path, 'rb') as qr_file:
                    qr_image = MIMEImage(qr_file.read())
                    qr_image.add_header('Content-ID', '<payment_qr>')
                    qr_image.add_header('Content-Disposition', 'inline', filename='payment_qr.png')
                    msg.attach(qr_image)
            except Exception as qr_error:
                print(f"Warning: Could not attach QR code to invoice email: {str(qr_error)}")
        
        smtp_port = int(smtp_port or 587)
        
        print(f"✓ Sending invoice email to {customer_email} using SMTP {smtp_server}:{smtp_port} from {smtp_username}")
        
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
                server.login(smtp_username, smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(smtp_username, smtp_password)
                server.send_message(msg)
        
        return {"success": True, "message": f"Invoice sent successfully to {customer_email}"}
    except Exception as e:
        print(f"Failed to send invoice email: {str(e)}")
        return {"success": False, "message": f"Failed to send email: {str(e)}"}
        return False

@app.post("/api/customers/{customer_id}/renew")
async def renew_customer(customer_id: str, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Renew customer subscription with optional invoice generation"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Customer
    from datetime import datetime
    from services.billing import renew_customer_core, send_invoice_email_sync
    
    try:
        data = await request.json()
        
        # Validate required fields
        start_from = data.get('start_from', 'today')
        period_months = data.get('period_months')
        with_invoice = data.get('with_invoice', False)
        
        if not period_months:
            return {"success": False, "message": "period_months is required"}
        
        try:
            period_months = int(period_months)
        except:
            return {"success": False, "message": "period_months must be a number"}
        
        customer = db.query(Customer).filter(
            Customer.customer_id == customer_id,
            Customer.company_id == company_id
        ).first()
        
        if not customer:
            return {"success": False, "message": "Customer not found"}
        
        if start_from == 'date':
            start_date_str = data.get('start_date')
            if not start_date_str:
                return {"success": False, "message": "start_date is required when start_from is 'date'"}
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            except ValueError:
                return {"success": False, "message": "Invalid start_date format. Use YYYY-MM-DD"}
        else:
            start_date = datetime.now()
        
        result = renew_customer_core(
            company_id=company_id,
            customer_id=customer_id,
            start_date=start_date,
            period_months=period_months,
            with_invoice=with_invoice,
            db=db,
            source="manual"
        )
        
        if not result.get('success'):
            return result
        
        print(f"DEBUG Manual Renew: with_invoice={with_invoice}, has_customer_email={bool(result.get('customer_email'))}, has_pdf_data={bool(result.get('pdf_data'))}, has_invoice_data={bool(result.get('invoice_data'))}, has_company_data={bool(result.get('company_data'))}")
        if result.get('customer_email'):
            print(f"DEBUG: customer_email={result.get('customer_email')}")
        if result.get('company_data'):
            print(f"DEBUG: company_data has smtp_server={bool(result.get('company_data', {}).get('smtp_server'))}, smtp_port={result.get('company_data', {}).get('smtp_port')}")
        
        if with_invoice and result.get('customer_email') and result.get('pdf_data'):
            try:
                email_result = await send_invoice_email(
                    result.get('invoice_data'),
                    result.get('customer_email'),
                    result.get('company_data'),
                    result.get('pdf_data'),
                    result.get('customer_type', 'PREPAID')
                )
                if email_result and email_result.get('success'):
                    print(f"✓ Invoice email sent to {result.get('customer_email')} for manual renew")
                else:
                    print(f"✗ Failed to send invoice email for manual renew: {email_result.get('message', 'Unknown error') if email_result else 'No result returned'}")
            except Exception as email_error:
                print(f"✗ Exception sending invoice email for manual renew: {str(email_error)}")
                import traceback
                traceback.print_exc()
        
        return {
            "success": True,
            "message": result.get('message'),
            "customer_id": customer_id,
            "start_date": result.get('start_date'),
            "end_date": result.get('end_date'),
            "invoice_no": result.get('invoice_no'),
            "transaction_id": result.get('transaction_id')
        }
    
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

@app.post("/api/customers/{customer_id}/balance-adjustment")
async def adjust_customer_balance(customer_id: str, request: Request, db: Session = Depends(get_db)):
    """Manually adjust customer balance (add due/extra amount)"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Customer, Transaction
    
    try:
        data = await request.json()
        amount = float(data.get("amount", 0))
        
        if amount == 0:
            return {"success": False, "message": "Amount cannot be zero"}
        
        customer = db.query(Customer).filter(
            Customer.customer_id == customer_id,
            Customer.company_id == company_id
        ).first()
        
        if not customer:
            return {"success": False, "message": "Customer not found"}
        
        current_balance = calculate_total_due(customer, db)
        
        if amount > 0:
            customer.total_bill_amount = (customer.total_bill_amount or 0) + amount
        else:
            customer.discount_credit = (customer.discount_credit or 0) + abs(amount)
        
        transaction = Transaction(
            company_id=company_id,
            customer_id=customer_id,
            transaction_type="manual_adjustment",
            amount=abs(amount),
            remarks=f"Manual balance adjustment: {'Added due' if amount > 0 else 'Credit applied'} ₹{round(abs(amount))}"
        )
        
        db.add(transaction)
        db.commit()
        
        new_balance = calculate_total_due(customer, db)
        
        return {
            "success": True,
            "message": f"Balance updated successfully. {'Added' if amount > 0 else 'Reduced'} ₹{round(abs(amount))}",
            "new_balance": new_balance
        }
    
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

@app.post("/api/customers/{customer_id}/renew/revert")
async def revert_last_renew(customer_id: str, request: Request, db: Session = Depends(get_db)):
    """Revert the last renewal for a customer"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Customer, Transaction
    from datetime import datetime, timedelta
    
    try:
        customer = db.query(Customer).filter(
            Customer.customer_id == customer_id,
            Customer.company_id == company_id
        ).first()
        
        if not customer:
            return {"success": False, "message": "Customer not found"}
        
        # Find the last renewal transaction
        last_renewal = db.query(Transaction).filter(
            Transaction.customer_id == customer_id,
            Transaction.company_id == company_id,
            Transaction.remarks.like("%Renewed for%")
        ).order_by(Transaction.payment_date.desc()).first()
        
        if not last_renewal:
            return {"success": False, "message": "No renewal transaction found to revert"}
        
        import re
        match = re.search(r'Renewed for (\d+) month', last_renewal.remarks)
        if not match:
            return {"success": False, "message": "Could not determine renewal period"}
        
        months_renewed = int(match.group(1))
        
        current_end_date = datetime.strptime(customer.end_date, "%Y-%m-%d")
        previous_end_date = current_end_date - timedelta(days=months_renewed * 30)
        
        customer.end_date = previous_end_date.strftime("%Y-%m-%d")
        
        # Reverse the transaction amount from balance
        current_balance = parse_currency(customer.balance)
        reversed_balance = current_balance + float(last_renewal.amount)
        customer.balance = f"₹{round(reversed_balance)}"
        
        last_renewal.status = "Reversed"
        last_renewal.remarks = last_renewal.remarks + " (REVERSED)"
        
        reversal_txn_no = generate_transaction_no(db, company_id)
        reversal_transaction = Transaction(
            company_id=company_id,
            customer_id=customer_id,
            transaction_no=reversal_txn_no,
            amount=float(last_renewal.amount),
            payment_mode="Reversal",
            payment_date=datetime.now().strftime("%Y-%m-%d"),
            status="Completed",
            remarks=f"Reversal of renewal transaction {last_renewal.transaction_no}"
        )
        
        db.add(reversal_transaction)
        db.commit()
        
        return {
            "success": True,
            "message": f"Last renewal reverted successfully. End date restored to {customer.end_date}",
            "start_date": customer.start_date,
            "end_date": customer.end_date
        }
    
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

@app.get("/admin/transactions", response_class=HTMLResponse)
async def admin_transactions(request: Request, db: Session = Depends(get_db)):
    """Transaction History page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="transactions")
    return templates.TemplateResponse("admin_transactions.html", context)

@app.get("/api/transactions/list")
async def list_transactions(request: Request, db: Session = Depends(get_db)):
    """Get all transactions with filtering"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Transaction, Customer
    
    try:
        query = db.query(Transaction, Customer).join(
            Customer, Transaction.customer_id == Customer.customer_id
        ).filter(Transaction.company_id == company_id)
        
        status = request.query_params.get('status')
        customer_id = request.query_params.get('customer_id')
        payment_mode = request.query_params.get('payment_mode')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        
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
        
        results = query.order_by(Transaction.payment_date.desc()).all()
        
        transactions = []
        for transaction, customer in results:
            transactions.append({
                "transaction_no": transaction.transaction_no,
                "customer_id": customer.customer_id,
                "customer_name": customer.customer_name,
                "amount": float(transaction.amount),
                "payment_mode": transaction.payment_mode,
                "payment_date": transaction.payment_date,
                "status": transaction.status,
                "remarks": transaction.remarks or ""
            })
        
        return {"success": True, "transactions": transactions}
    
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/admin/complaints", response_class=HTMLResponse)
async def admin_complaints(request: Request, db: Session = Depends(get_db)):
    """Complaints List page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="complaints")
    return templates.TemplateResponse("admin_complaints.html", context)

@app.get("/api/complaints/list")
async def list_complaints(request: Request, db: Session = Depends(get_db)):
    """Get all complaints with filtering"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Complaint, Customer
    
    try:
        query = db.query(Complaint, Customer).join(
            Customer, Complaint.customer_id == Customer.customer_id
        ).filter(Complaint.company_id == company_id)
        
        status = request.query_params.get('status')
        priority = request.query_params.get('priority')
        complaint_type = request.query_params.get('complaint_type')
        
        if status:
            query = query.filter(Complaint.status == status)
        if priority:
            query = query.filter(Complaint.priority == priority)
        if complaint_type:
            query = query.filter(Complaint.complaint_type == complaint_type)
        
        results = query.order_by(Complaint.created_at.desc()).all()
        
        complaints = []
        for complaint, customer in results:
            complaints.append({
                "id": complaint.id,
                "ticket_no": complaint.ticket_no,
                "customer_id": customer.customer_id,
                "customer_name": customer.customer_name,
                "complaint_type": complaint.complaint_type,
                "priority": complaint.priority,
                "description": complaint.description,
                "status": complaint.status,
                "created_at": complaint.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                "resolved_at": complaint.resolved_at.strftime('%Y-%m-%d %H:%M:%S') if complaint.resolved_at else None
            })
        
        return {"success": True, "complaints": complaints}
    
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.post("/api/complaints/{complaint_id}/update-status")
async def update_complaint_status(complaint_id: int, request: Request, db: Session = Depends(get_db)):
    """Update complaint status"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Complaint
    
    try:
        body = await request.json()
        new_status = body.get('status')
        
        if not new_status:
            return {"success": False, "message": "Status is required"}
        
        complaint = db.query(Complaint).filter(
            Complaint.id == complaint_id,
            Complaint.company_id == company_id
        ).first()
        
        if not complaint:
            return {"success": False, "message": "Complaint not found"}
        
        complaint.status = new_status
        if new_status == 'Resolved':
            complaint.resolved_at = datetime.utcnow()
        
        db.commit()
        
        return {"success": True, "message": "Complaint status updated successfully"}
    
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

@app.get("/admin/addon-bills", response_class=HTMLResponse)
async def admin_addon_bills(request: Request, db: Session = Depends(get_db)):
    """Addon Bills page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="addon-bills")
    return templates.TemplateResponse("admin_addon_bills.html", context)

@app.get("/admin/revenue", response_class=HTMLResponse)
async def admin_revenue(request: Request, db: Session = Depends(get_db)):
    """Revenue List page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="revenue")
    return templates.TemplateResponse("admin_revenue.html", context)

@app.get("/admin/employees", response_class=HTMLResponse)
async def admin_employees(request: Request, db: Session = Depends(get_db)):
    """Employee Management page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="employees")
    return templates.TemplateResponse("admin_employees.html", context)


@app.get("/admin/locations", response_class=HTMLResponse)
async def admin_locations(request: Request, db: Session = Depends(get_db)):
    """Locations Management page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="locations")
    return templates.TemplateResponse("admin_locations.html", context)

@app.get("/api/locations/list")
async def list_locations(
    request: Request, 
    service_type: str = None,
    db: Session = Depends(get_db)
):
    """Get locations for the company, optionally filtered by service_type usage"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Location, Customer
    from sqlalchemy import func, select
    
    try:
        if service_type:
            loc_names_subq = (
                db.query(func.distinct(func.lower(func.trim(Customer.locality))).label('loc_name'))
                .filter(
                    Customer.company_id == company_id,
                    Customer.service_type == service_type,
                    Customer.locality.isnot(None),
                    func.trim(Customer.locality) != ''
                )
                .subquery()
            )
            
            locations = db.query(Location).filter(
                Location.company_id == company_id,
                func.lower(func.trim(Location.name)).in_(select(loc_names_subq.c.loc_name))
            ).order_by(Location.name).all()
        else:
            # Return all locations
            locations = db.query(Location).filter(
                Location.company_id == company_id
            ).order_by(Location.name).all()
        
        return {
            "success": True,
            "locations": [
                {
                    "id": loc.id,
                    "name": loc.name,
                    "city": loc.city or "",
                    "state": loc.state or "",
                    "pincode": loc.pincode or "",
                    "status": loc.status,
                    "created_at": loc.created_at.strftime("%Y-%m-%d %H:%M:%S") if loc.created_at else ""
                }
                for loc in locations
            ]
        }
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.post("/api/locations/create")
async def create_location(request: Request, db: Session = Depends(get_db)):
    """Create a new location"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Location
    
    try:
        form_data = await request.form()
        
        location = Location(
            company_id=company_id,
            name=form_data.get("name"),
            city=form_data.get("city"),
            state=form_data.get("state"),
            pincode=form_data.get("pincode"),
            status=form_data.get("status", "Active")
        )
        
        db.add(location)
        db.commit()
        db.refresh(location)
        
        return {"success": True, "message": "Location created successfully", "location_id": location.id}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

@app.post("/api/locations/update")
async def update_location(request: Request, db: Session = Depends(get_db)):
    """Update an existing location"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Location
    
    try:
        form_data = await request.form()
        location_id = form_data.get("location_id")
        
        location = db.query(Location).filter(
            Location.id == location_id,
            Location.company_id == company_id
        ).first()
        
        if not location:
            return {"success": False, "message": "Location not found"}
        
        location.name = form_data.get("name", location.name)
        location.city = form_data.get("city", location.city)
        location.state = form_data.get("state", location.state)
        location.pincode = form_data.get("pincode", location.pincode)
        location.status = form_data.get("status", location.status)
        
        db.commit()
        
        return {"success": True, "message": "Location updated successfully"}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

@app.post("/api/locations/delete")
async def delete_location(request: Request, db: Session = Depends(get_db), _ = Depends(require_not_employee)):
    """Delete a location"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Location
    
    try:
        form_data = await request.form()
        location_id = form_data.get("location_id")
        
        location = db.query(Location).filter(
            Location.id == location_id,
            Location.company_id == company_id
        ).first()
        
        if not location:
            return {"success": False, "message": "Location not found"}
        
        db.delete(location)
        db.commit()
        
        return {"success": True, "message": "Location deleted successfully"}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

@app.get("/admin/support", response_class=HTMLResponse)
async def admin_support(request: Request, db: Session = Depends(get_db)):
    """Support List page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="support")
    return templates.TemplateResponse("admin_support.html", context)

@app.get("/admin/notifications", response_class=HTMLResponse)
async def admin_notifications(request: Request, db: Session = Depends(get_db)):
    """Notifications page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="notifications")
    return templates.TemplateResponse("admin_notifications.html", context)

@app.get("/admin/whatsapp-campaign", response_class=HTMLResponse)
async def admin_whatsapp_campaign(request: Request, db: Session = Depends(get_db)):
    """WhatsApp Campaign page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="whatsapp-campaign")
    return templates.TemplateResponse("admin_whatsapp_campaign.html", context)

@app.get("/admin/whatsapp-templates", response_class=HTMLResponse)
async def admin_whatsapp_templates(request: Request, db: Session = Depends(get_db)):
    """WhatsApp Templates page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="whatsapp-templates")
    return templates.TemplateResponse("admin_whatsapp_templates.html", context)

@app.get("/admin/reports", response_class=HTMLResponse)
async def admin_reports(request: Request, db: Session = Depends(get_db)):
    """Reports page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="reports")
    return templates.TemplateResponse("admin_reports.html", context)

@app.get("/admin/customer-distribution", response_class=HTMLResponse)
async def admin_customer_distribution(request: Request, db: Session = Depends(get_db)):
    """Customer Distribution page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="customer-distribution")
    return templates.TemplateResponse("admin_customer_distribution.html", context)

@app.get("/api/customer-distribution/list")
async def get_customer_distribution_list(
    request: Request,
    connection_type: str = "Broadband",
    search: str = "",
    letter: str = "",
    db: Session = Depends(get_db)
):
    """Get list of employees with their locality assignments"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from sqlalchemy import func, distinct, select
    from database import Employee, EmployeeLocalityAssignment, Location, Customer
    
    # Correlated subquery to count distinct customers for employee's assigned locations
    # This avoids the cartesian product that causes duplicate location names
    subq_count = (
        select(func.count(distinct(Customer.id)))
        .select_from(Customer)
        .join(
            Location,
            (func.lower(func.trim(Customer.locality)) == func.lower(func.trim(Location.name))) &
            (Customer.company_id == company_id)
        )
        .join(
            EmployeeLocalityAssignment,
            (EmployeeLocalityAssignment.location_id == Location.id) &
            (EmployeeLocalityAssignment.connection_type == connection_type) &
            (EmployeeLocalityAssignment.active == True)
        )
        .where(
            EmployeeLocalityAssignment.employee_id == Employee.id,
            Customer.company_id == company_id,
            Customer.service_type == connection_type,
            Customer.status == 'Active'
        )
        .correlate(Employee)
        .scalar_subquery()
    )
    
    query = db.query(
        Employee.id,
        Employee.employee_code,
        Employee.employee_name,
        func.group_concat(Location.name).label('assigned_localities'),
        subq_count.label('subscriber_count')
    ).outerjoin(
        EmployeeLocalityAssignment,
        (EmployeeLocalityAssignment.employee_id == Employee.id) &
        (EmployeeLocalityAssignment.connection_type == connection_type) &
        (EmployeeLocalityAssignment.active == True)
    ).outerjoin(
        Location,
        Location.id == EmployeeLocalityAssignment.location_id
    ).filter(
        Employee.company_id == company_id,
        Employee.is_deleted == False
    )
    
    if search:
        query = query.filter(
            (Employee.employee_name.ilike(f"%{search}%")) |
            (Employee.employee_code.ilike(f"%{search}%"))
        )
    
    if letter and letter != "All":
        query = query.filter(Employee.employee_name.ilike(f"{letter}%"))
    
    query = query.group_by(Employee.id, Employee.employee_code, Employee.employee_name).order_by(Employee.employee_name)
    
    employees = query.all()
    
    result = []
    for idx, emp in enumerate(employees, 1):
        result.append({
            "sno": idx,
            "employee_id": emp.id,
            "employee_code": emp.employee_code,
            "employee_name": emp.employee_name,
            "assigned_localities": emp.assigned_localities or "",
            "subscriber_count": emp.subscriber_count or 0,
            "connection_type": connection_type
        })
    
    return {"employees": result, "connection_type": connection_type}

@app.get("/admin/customer-distribution/assign/{connection_type}", response_class=HTMLResponse)
async def assign_locality_page(
    request: Request,
    connection_type: str,
    db: Session = Depends(get_db)
):
    """Assign Locality page for Cable or Broadband"""
    auth_check = require_auth(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="customer-distribution")
    context["connection_type"] = connection_type
    return templates.TemplateResponse("admin_assign_locality.html", context)

@app.get("/admin/customer-distribution/edit/{employee_id}/{connection_type}", response_class=HTMLResponse)
async def edit_employee_locality(
    request: Request,
    employee_id: int,
    connection_type: str,
    db: Session = Depends(get_db)
):
    """Edit employee locality assignments"""
    auth_check = require_auth(request)
    if auth_check:
        return auth_check
    
    from database import Employee, EmployeeLocalityAssignment, Location
    
    company_id = request.session.get("company_id")
    
    employee = db.query(Employee).filter(
        Employee.id == employee_id,
        Employee.company_id == company_id
    ).first()
    
    if not employee:
        return RedirectResponse(url="/admin/customer-distribution", status_code=303)
    
    assigned_localities = db.query(Location).join(
        EmployeeLocalityAssignment,
        (EmployeeLocalityAssignment.location_id == Location.id) &
        (EmployeeLocalityAssignment.employee_id == employee_id) &
        (EmployeeLocalityAssignment.connection_type == connection_type) &
        (EmployeeLocalityAssignment.active == True)
    ).filter(Location.company_id == company_id).all()
    
    from database import Customer
    from sqlalchemy import func, select
    
    loc_names_subq = (
        db.query(func.distinct(func.lower(func.trim(Customer.locality))).label('loc_name'))
        .filter(
            Customer.company_id == company_id,
            Customer.service_type == connection_type,
            Customer.locality.isnot(None),
            func.trim(Customer.locality) != ''
        )
        .subquery()
    )
    
    all_localities = db.query(Location).filter(
        Location.company_id == company_id,
        func.lower(func.trim(Location.name)).in_(select(loc_names_subq.c.loc_name))
    ).order_by(Location.name).all()
    
    context = get_admin_context(request, db, active_page="customer-distribution")
    context.update({
        "employee": employee,
        "connection_type": connection_type,
        "assigned_localities": assigned_localities,
        "all_localities": all_localities
    })
    return templates.TemplateResponse("admin_edit_employee_locality.html", context)

@app.post("/api/customer-distribution/assign")
async def assign_localities_to_employee(
    request: Request,
    db: Session = Depends(get_db)
):
    """Assign localities to an employee"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    from database import EmployeeLocalityAssignment
    
    company_id = request.session.get("company_id")
    admin_id = request.session.get("user_id")
    
    data = await request.json()
    
    # Validate and coerce employee_id
    employee_id_raw = data.get("employee_id")
    try:
        employee_id = int(employee_id_raw) if employee_id_raw else None
        if not employee_id or employee_id <= 0:
            return {"success": False, "message": "Invalid employee_id"}
    except (TypeError, ValueError):
        return {"success": False, "message": "Invalid employee_id"}
    
    # Validate and coerce location_ids
    location_ids_raw = data.get("location_ids", [])
    try:
        location_ids = [int(x) for x in location_ids_raw if x is not None and str(x).strip()]
        location_ids = [x for x in location_ids if x > 0]  # Filter out invalid IDs
    except (TypeError, ValueError):
        return {"success": False, "message": "Invalid location_ids"}
    
    if not location_ids:
        return {"success": False, "message": "At least one location is required"}
    
    connection_type = data.get("connection_type", "Broadband")
    
    try:
        for location_id in location_ids:
            existing = db.query(EmployeeLocalityAssignment).filter(
                EmployeeLocalityAssignment.company_id == company_id,
                EmployeeLocalityAssignment.employee_id == employee_id,
                EmployeeLocalityAssignment.location_id == location_id,
                EmployeeLocalityAssignment.connection_type == connection_type
            ).first()
            
            if not existing:
                assignment = EmployeeLocalityAssignment(
                    company_id=company_id,
                    employee_id=employee_id,
                    location_id=location_id,
                    connection_type=connection_type,
                    active=True,
                    created_by=admin_id
                )
                db.add(assignment)
        
        db.commit()
        return {"success": True, "message": "Localities assigned successfully"}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

@app.post("/api/customer-distribution/update")
async def update_employee_localities(
    request: Request,
    db: Session = Depends(get_db)
):
    """Update employee locality assignments (replace existing)"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    from database import EmployeeLocalityAssignment
    
    company_id = request.session.get("company_id")
    admin_id = request.session.get("user_id")
    
    data = await request.json()
    
    # Validate and coerce employee_id
    employee_id_raw = data.get("employee_id")
    try:
        employee_id = int(employee_id_raw) if employee_id_raw else None
        if not employee_id or employee_id <= 0:
            return {"success": False, "message": "Invalid employee_id"}
    except (TypeError, ValueError):
        return {"success": False, "message": "Invalid employee_id"}
    
    # Validate and coerce location_ids
    location_ids_raw = data.get("location_ids", [])
    try:
        location_ids = [int(x) for x in location_ids_raw if x is not None and str(x).strip()]
        location_ids = [x for x in location_ids if x > 0]  # Filter out invalid IDs
    except (TypeError, ValueError):
        return {"success": False, "message": "Invalid location_ids"}
    
    connection_type = data.get("connection_type", "Broadband")
    
    try:
        db.query(EmployeeLocalityAssignment).filter(
            EmployeeLocalityAssignment.company_id == company_id,
            EmployeeLocalityAssignment.employee_id == employee_id,
            EmployeeLocalityAssignment.connection_type == connection_type
        ).update({"active": False, "updated_by": admin_id})
        
        for location_id in location_ids:
            existing = db.query(EmployeeLocalityAssignment).filter(
                EmployeeLocalityAssignment.company_id == company_id,
                EmployeeLocalityAssignment.employee_id == employee_id,
                EmployeeLocalityAssignment.location_id == location_id,
                EmployeeLocalityAssignment.connection_type == connection_type
            ).first()
            
            if existing:
                existing.active = True
                existing.updated_by = admin_id
            else:
                assignment = EmployeeLocalityAssignment(
                    company_id=company_id,
                    employee_id=employee_id,
                    location_id=location_id,
                    connection_type=connection_type,
                    active=True,
                    created_by=admin_id
                )
                db.add(assignment)
        
        db.commit()
        return {"success": True, "message": "Localities updated successfully"}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

@app.get("/admin/track-employee", response_class=HTMLResponse)
async def admin_track_employee(request: Request, db: Session = Depends(get_db)):
    """Track Employee page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="track-employee")
    return templates.TemplateResponse("admin_track_employee.html", context)

@app.get("/admin/data-management", response_class=HTMLResponse)
async def admin_data_management(request: Request, db: Session = Depends(get_db)):
    """Data Management page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="data-management")
    return templates.TemplateResponse("admin_data_management.html", context)

@app.get("/admin/send-invoices", response_class=HTMLResponse)
async def admin_send_invoices(request: Request, db: Session = Depends(get_db)):
    """Send Invoices List page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="send-invoices")
    return templates.TemplateResponse("admin_send_invoices.html", context)

@app.get("/admin/book-connection", response_class=HTMLResponse)
async def admin_book_connection(request: Request, db: Session = Depends(get_db)):
    """Book Connection page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="book-connection")
    return templates.TemplateResponse("admin_book_connection.html", context)

@app.get("/admin/connection-request", response_class=HTMLResponse)
async def admin_connection_request(request: Request, db: Session = Depends(get_db)):
    """Connection Request page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="connection-request")
    return templates.TemplateResponse("admin_connection_request.html", context)

@app.get("/admin/expenses", response_class=HTMLResponse)
async def admin_expenses(request: Request, db: Session = Depends(get_db)):
    """Expense List page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="expenses")
    return templates.TemplateResponse("admin_expenses.html", context)

@app.get("/admin/revenue-expense", response_class=HTMLResponse)
async def admin_revenue_expense(request: Request, db: Session = Depends(get_db)):
    """Revenue & Expense page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="revenue-expense")
    return templates.TemplateResponse("admin_revenue_expense.html", context)

@app.get("/admin/deleted-users", response_class=HTMLResponse)
async def admin_deleted_users(request: Request, db: Session = Depends(get_db)):
    """Deleted Users page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="deleted-users")
    return templates.TemplateResponse("admin_deleted_users.html", context)

@app.get("/admin/sms-logs", response_class=HTMLResponse)
async def admin_sms_logs(request: Request, db: Session = Depends(get_db)):
    """SMS Logs page"""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    
    context = get_admin_context(request, db, active_page="sms-logs")
    return templates.TemplateResponse("admin_sms_logs.html", context)

@app.get("/api/customers/{customer_id}/download-caf")
async def download_caf(customer_id: str, request: Request, db: Session = Depends(get_db)):
    """Download CAF PDF for a customer"""
    from fastapi.responses import Response
    from database import Customer, Company, Plan
    
    auth_check = require_auth(request)
    if auth_check:
        return {"error": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    try:
        customer = db.query(Customer).filter(
            Customer.customer_id == customer_id,
            Customer.company_id == company_id
        ).first()
        
        if not customer:
            return {"success": False, "message": "Customer not found"}
        
        if customer.caf_pdf:
            return Response(
                content=customer.caf_pdf,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f"attachment; filename=CAF_{customer.caf_no or customer_id}.pdf"
                }
            )
        else:
            company = db.query(Company).filter(Company.company_id == company_id).first()
            company_data = {
                'company_name': company.company_name if company else 'AUTO ISP BILLING',
                'company_address': company.company_address if company else '',
                'company_phone': company.company_phone if company else '',
                'company_email': company.company_email if company else '',
                'logo_path': company.logo_path if company and company.logo_path else '',
                'city': company.city if company else '',
                'state': company.state if company else ''
            }
            
            plan = db.query(Plan).filter(Plan.id == customer.plan_id).first() if customer.plan_id else None
            plan_name = plan.plan_name if plan else 'Broadband'
            
            caf_customer_data = {
                'caf_no': customer.caf_no or '',
                'customer_id': customer.customer_id or '',
                'installation_date': customer.installation_date or '',
                'customer_name': customer.customer_name or '',
                'username': customer.username or '',
                'customer_email': customer.customer_email or '',
                'customer_phone': customer.customer_phone or '',
                'alt_mobile': customer.alt_mobile or '',
                'id_proof': customer.id_proof or '',
                'id_proof_no': customer.id_proof_no or '',
                'address': customer.address or '',
                'locality': customer.locality or '',
                'city': customer.city or '',
                'state': customer.state or '',
                'pincode': customer.pincode or '',
                'service_type': customer.service_type or '',
                'plan_name': plan_name or '',
                'customer_type': customer.customer_type or '',
                'monthly_amount': customer.monthly_amount or 0,
                'start_date': customer.start_date or '',
                'end_date': customer.end_date or '',
                'mac_address': customer.mac_address or '',
                'ip_address': customer.ip_address or ''
            }
            
            caf_pdf_data = generate_caf_pdf(caf_customer_data, company_data)
            
            customer.caf_pdf = caf_pdf_data
            db.commit()
            
            return Response(
                content=caf_pdf_data,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f"attachment; filename=CAF_{customer.caf_no or customer_id}.pdf"
                }
            )
    
    except Exception as e:
        return {"success": False, "message": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

@app.post("/api/customers/{customer_id}/addon-bill")
async def generate_addon_bill(customer_id: str, request: Request, db: Session = Depends(get_db)):
    """Generate addon/manual invoice for customer"""
    auth_check = require_auth(request)
    if auth_check:
        return {"success": False, "message": "Unauthorized"}
    
    company_id = request.session.get("company_id")
    
    from database import Customer, Company, Invoice, Payment
    from datetime import datetime, timedelta
    
    def safe_float(value, default=0.0):
        """Safely parse float value, handling empty strings and None"""
        try:
            if value is None or value == '':
                return default
            return float(value)
        except (TypeError, ValueError):
            return default
    
    try:
        data = await request.json()
        
        description = data.get('description', '').strip()
        amount = safe_float(data.get('amount'), 0)
        cgst_percent = safe_float(data.get('cgst'), 0)
        sgst_percent = safe_float(data.get('sgst'), 0)
        igst_percent = safe_float(data.get('igst'), 0)
        
        if not description:
            return {"success": False, "message": "Description is required"}
        
        if amount <= 0:
            return {"success": False, "message": "Amount must be greater than 0"}
        
        # Fetch customer
        customer = db.query(Customer).filter(
            Customer.customer_id == customer_id,
            Customer.company_id == company_id
        ).first()
        
        if not customer:
            return {"success": False, "message": "Customer not found"}
        
        # Fetch company
        company = db.query(Company).filter(Company.company_id == company_id).first()
        if not company:
            return {"success": False, "message": "Company not found"}
        
        # Calculate taxes only if explicitly provided
        if cgst_percent > 0 or sgst_percent > 0 or igst_percent > 0:
            cgst_amount = round((amount * cgst_percent) / 100, 2)
            sgst_amount = round((amount * sgst_percent) / 100, 2)
            igst_amount = round((amount * igst_percent) / 100, 2)
        else:
            cgst_amount = 0.0
            sgst_amount = 0.0
            igst_amount = 0.0
        
        total_tax = cgst_amount + sgst_amount + igst_amount
        total_amount = round(amount + total_tax, 2)
        
        prev_due_total = calculate_total_due(customer, db)
        
        # Get all unpaid invoices for this customer to show in description
        previous_invoices_data = []
        if prev_due_total > 0:
            all_invoices = db.query(Invoice).filter(
                Invoice.company_id == company_id,
                Invoice.customer_id == customer_id
            ).order_by(Invoice.issue_date.asc()).all()
            
            all_payments = db.query(Payment).filter(
                Payment.company_id == company_id,
                Payment.customer_id == customer_id
            ).all()
            total_payments = sum(p.amount for p in all_payments)
            
            remaining_payments = total_payments
            for inv in all_invoices:
                invoice_amount = inv.total_amount
                allocated = min(invoice_amount, remaining_payments)
                remaining_payments -= allocated
                outstanding = invoice_amount - allocated
                
                if outstanding > 0.01:  # Include invoices with outstanding balance > 1 paisa
                    previous_invoices_data.append({
                        'invoice_no': inv.invoice_no,
                        'amount': round(outstanding, 2)
                    })
        
        # Generate invoice number
        invoice_no = generate_invoice_number(company_id, db)
        
        # Prepare invoice data
        today = datetime.now()
        due_date = today + timedelta(days=7)
        
        invoice_data = {
            'invoice_no': invoice_no,
            'invoice_type': 'addon',
            'issue_date': today.strftime('%Y-%m-%d'),
            'due_date': due_date.strftime('%Y-%m-%d'),
            'customer_name': customer.customer_name,
            'customer_id': customer_id,
            'description': description,
            'base_amount': amount,
            'cgst': cgst_amount,
            'sgst': sgst_amount,
            'igst': igst_amount,
            'cgst_percent': cgst_percent,
            'sgst_percent': sgst_percent,
            'igst_percent': igst_percent,
            'total_tax': total_tax,
            'total_amount': total_amount,
            'prev_due_total': prev_due_total,
            'grand_total': round(total_amount + prev_due_total, 2)
        }
        
        company_data = {
            'company_name': company.company_name,
            'company_email': company.company_email,
            'company_phone': company.company_phone,
            'company_address': company.company_address,
            'state': company.state,
            'city': company.city,
            'pincode': company.pincode,
            'gst_number': company.gst_number,
            'bank_name': company.bank_name,
            'account_number': company.account_number,
            'branch_ifsc': company.branch_ifsc,
            'upi_id': company.upi_id,
            'declaration': company.declaration or '',
            'terms_conditions': company.terms_conditions or ''
        }
        
        customer_data = {
            'customer_name': customer.customer_name,
            'customer_email': customer.customer_email,
            'customer_phone': customer.customer_phone,
            'mobile': customer.customer_phone,
            'username': customer.username or customer.customer_id,
            'address': customer.address,
            'city': customer.city,
            'state': customer.state,
            'pincode': customer.pincode,
            'customer_gst_no': customer.customer_gst_no,
            'billing_type': customer.customer_type or 'PREPAID',
            'category': customer.service_type or 'Broadband',
            'gst_invoice_needed': customer.gst_invoice_needed or 'No'
        }
        
        # Generate PDF
        pdf_data = generate_invoice_pdf(invoice_data, company_data, customer_data, previous_invoices_data if prev_due_total > 0 else None)
        
        import os
        pdf_dir = "/var/lib/autoispbilling/invoices"
        os.makedirs(pdf_dir, exist_ok=True)
        pdf_filename = f"{invoice_no}.pdf"
        pdf_path = os.path.join(pdf_dir, pdf_filename)
        
        with open(pdf_path, 'wb') as f:
            f.write(pdf_data)
        
        # Save invoice to database
        from database import Transaction
        invoice = Invoice(
            company_id=company_id,
            customer_id=customer_id,
            invoice_no=invoice_no,
            issue_date=today.strftime('%Y-%m-%d'),
            due_date=due_date.strftime('%Y-%m-%d'),
            base_amount=amount,
            cgst_tax=cgst_amount,
            sgst_tax=sgst_amount,
            igst_tax=igst_amount,
            total_amount=total_amount,
            pdf_path=pdf_path,
            status='generated'
        )
        
        db.add(invoice)
        db.flush()
        
        transaction = Transaction(
            company_id=company_id,
            customer_id=customer_id,
            transaction_type='addon_invoice',
            amount=total_amount,
            invoice_id=invoice.id,
            remarks=f"Addon invoice {invoice_no} generated - {description}"
        )
        db.add(transaction)
        
        # Update customer's total bill amount
        customer.total_bill_amount = (customer.total_bill_amount or 0) + total_amount
        db.commit()
        
        try:
            customer_email = (customer.customer_email or '').strip()
            if customer_email:
                company_data = {
                    "company_name": company.company_name,
                    "company_address": company.company_address,
                    "company_phone": company.company_phone,
                    "company_email": company.company_email,
                    "gst_number": company.gst_number,
                    "smtp_server": company.smtp_server,
                    "smtp_port": company.smtp_port,
                    "smtp_username": company.smtp_username,
                    "smtp_password": company.smtp_password
                }
                
                result = await send_invoice_email(
                    invoice_data,
                    customer_email,
                    company_data,
                    pdf_data,
                    customer.customer_type or 'PREPAID'
                )
                
                if not result.get("success"):
                    print(f"Warning: Failed to send addon invoice email: {result.get('message')}")
            else:
                print(f"Warning: Customer {customer_id} has no email address, skipping email send")
        except Exception as email_error:
            print(f"Warning: Failed to send addon invoice email: {str(email_error)}")
        
        return {
            "success": True,
            "message": "Addon bill generated and emailed successfully",
            "invoice_no": invoice_no,
            "total_amount": total_amount,
            "grand_total": invoice_data['grand_total']
        }
    
    except Exception as e:
        db.rollback()
        print(f"Error generating addon bill: {str(e)}")
        return {"success": False, "message": f"Error generating addon bill: {str(e)}"}

# Employee Dashboard Routes
@app.get("/employee/dashboard", response_class=HTMLResponse)
async def employee_dashboard(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    
    context = get_employee_context(request, db, "dashboard")
    return templates.TemplateResponse("employee_dashboard.html", context)

@app.get("/employee/users", response_class=HTMLResponse)
async def employee_users(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    
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
    
    context = get_employee_context(request, db, "locations")
    return templates.TemplateResponse("employee_locations.html", context)

@app.get("/employee/transactions", response_class=HTMLResponse)
async def employee_transactions(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    
    context = get_employee_context(request, db, "transactions")
    return templates.TemplateResponse("employee_transactions.html", context)

@app.get("/employee/addon-bills", response_class=HTMLResponse)
async def employee_addon_bills(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    
    context = get_employee_context(request, db, "addon-bills")
    return templates.TemplateResponse("employee_addon_bills.html", context)

@app.get("/employee/send-invoices", response_class=HTMLResponse)
async def employee_send_invoices(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    
    context = get_employee_context(request, db, "send-invoices")
    return templates.TemplateResponse("employee_send_invoices.html", context)

@app.get("/employee/complaints", response_class=HTMLResponse)
async def employee_complaints(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    
    context = get_employee_context(request, db, "complaints")
    return templates.TemplateResponse("employee_complaints.html", context)

@app.get("/employee/notifications", response_class=HTMLResponse)
async def employee_notifications(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    
    context = get_employee_context(request, db, "notifications")
    return templates.TemplateResponse("employee_notifications.html", context)

@app.get("/employee/reports", response_class=HTMLResponse)
async def employee_reports(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    
    context = get_employee_context(request, db, "reports")
    return templates.TemplateResponse("employee_reports.html", context)

@app.get("/employee/data-management", response_class=HTMLResponse)
async def employee_data_management(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    
    context = get_employee_context(request, db, "data-management")
    return templates.TemplateResponse("employee_data_management.html", context)

@app.get("/employee/book-connection", response_class=HTMLResponse)
async def employee_book_connection(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    
    context = get_employee_context(request, db, "book-connection")
    return templates.TemplateResponse("employee_book_connection.html", context)

@app.get("/employee/connection-request", response_class=HTMLResponse)
async def employee_connection_request(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    
    context = get_employee_context(request, db, "connection-request")
    return templates.TemplateResponse("employee_connection_request.html", context)

@app.get("/employee/deleted-users", response_class=HTMLResponse)
async def employee_deleted_users(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    
    context = get_employee_context(request, db, "deleted-users")
    return templates.TemplateResponse("employee_deleted_users.html", context)

@app.get("/employee/sms-logs", response_class=HTMLResponse)
async def employee_sms_logs(request: Request, db: Session = Depends(get_db)):
    auth_check = require_employee(request)
    if auth_check:
        return auth_check
    
    context = get_employee_context(request, db, "sms-logs")
    return templates.TemplateResponse("employee_sms_logs.html", context)

# ============================================================================
# SUPERADMIN ROUTES
# ============================================================================

def get_superadmin_context(request: Request, db: Session):
    """Get context for superadmin templates (no company_id)"""
    user_name = request.session.get("user_name", "Super Administrator")
    return {
        "request": request,
        "user_name": user_name,
        "user_type": "superadmin"
    }

@app.get("/superadmin/dashboard", response_class=HTMLResponse)
async def superadmin_dashboard(request: Request, db: Session = Depends(get_db)):
    from database import Company, Admin
    
    auth_check = require_superadmin(request)
    if auth_check:
        return auth_check
    
    # Get statistics
    total_admins = db.query(Admin).count()
    active_admins = db.query(Company).filter(Company.status == "Active").count()
    deactive_admins = db.query(Company).filter(Company.status == "Deactive").count()
    suspended_admins = db.query(Company).filter(Company.status == "Suspended").count()
    
    # Calculate total dues and payments
    total_dues = db.query(func.sum(Company.balance_amount)).scalar() or 0
    # For now, total_payments is 0 (will be calculated from transaction history later)
    total_payments = 0
    
    # Get recent admins (last 10)
    recent_admins_query = db.query(
        Company.company_id,
        Company.company_name,
        Company.status,
        Company.end_date,
        Admin.admin_name,
        Admin.admin_email,
        Admin.admin_mobile
    ).join(Admin, Company.company_id == Admin.company_id).order_by(Company.created_at.desc()).limit(10).all()
    
    recent_admins = []
    for row in recent_admins_query:
        recent_admins.append({
            "company_id": row.company_id,
            "company_name": row.company_name,
            "status": row.status,
            "end_date": row.end_date,
            "admin_name": row.admin_name,
            "admin_email": row.admin_email,
            "admin_mobile": row.admin_mobile
        })
    
    context = get_superadmin_context(request, db)
    context.update({
        "active_page": "dashboard",
        "total_admins": total_admins,
        "active_admins": active_admins,
        "deactive_admins": deactive_admins,
        "suspended_admins": suspended_admins,
        "total_dues": total_dues,
        "total_payments": total_payments,
        "recent_admins": recent_admins
    })
    
    return templates.TemplateResponse("superadmin_dashboard.html", context)

@app.get("/superadmin/admins", response_class=HTMLResponse)
async def superadmin_admins(request: Request, db: Session = Depends(get_db)):
    auth_check = require_superadmin(request)
    if auth_check:
        return auth_check
    
    context = get_superadmin_context(request, db)
    context["active_page"] = "admin_management"
    
    return templates.TemplateResponse("superadmin_admins.html", context)


# Superadmin API endpoints
@app.get("/api/superadmin/packages")
async def get_superadmin_packages_api(request: Request, db: Session = Depends(get_db)):
    """Get active packages for dropdown (used in Add Admin form)"""
    from database import SuperAdminPackage
    
    auth_check = require_superadmin(request)
    if auth_check:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    # Get all active packages
    packages = db.query(SuperAdminPackage).filter(SuperAdminPackage.is_active == True).all()
    
    packages_data = []
    for pkg in packages:
        packages_data.append({
            "id": pkg.id,
            "package_name": pkg.package_name,
            "package_price": pkg.package_price,
            "description": pkg.description
        })
    
    return JSONResponse({"packages": packages_data})

@app.get("/api/superadmin/packages/all")
async def get_all_superadmin_packages_api(request: Request, db: Session = Depends(get_db)):
    """Get all packages including inactive (for package management page)"""
    from database import SuperAdminPackage
    
    auth_check = require_superadmin(request)
    if auth_check:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    # Get all packages
    packages = db.query(SuperAdminPackage).all()
    
    packages_data = []
    for pkg in packages:
        packages_data.append({
            "id": pkg.id,
            "package_name": pkg.package_name,
            "user_count": pkg.user_count,
            "package_type": pkg.package_type,
            "package_price": pkg.package_price,
            "description": pkg.description,
            "is_active": pkg.is_active,
            "created_at": pkg.created_at.isoformat() if pkg.created_at else None,
            "updated_at": pkg.updated_at.isoformat() if pkg.updated_at else None
        })
    
    return JSONResponse({"packages": packages_data})

@app.get("/api/superadmin/packages/{package_id}")
async def get_superadmin_package_api(request: Request, package_id: int, db: Session = Depends(get_db)):
    """Get single package by ID (for edit functionality)"""
    from database import SuperAdminPackage
    
    auth_check = require_superadmin(request)
    if auth_check:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    package = db.query(SuperAdminPackage).filter(SuperAdminPackage.id == package_id).first()
    if not package:
        return JSONResponse({"error": "Package not found"}, status_code=404)
    
    package_data = {
        "id": package.id,
        "package_name": package.package_name,
        "user_count": package.user_count,
        "package_type": package.package_type,
        "package_price": package.package_price,
        "description": package.description,
        "is_active": package.is_active,
        "created_at": package.created_at.isoformat() if package.created_at else None,
        "updated_at": package.updated_at.isoformat() if package.updated_at else None
    }
    
    return JSONResponse({"package": package_data})

@app.get("/api/superadmin/packages/{package_id}")
async def get_superadmin_package_api(request: Request, package_id: int, db: Session = Depends(get_db)):
    """Get single package by ID"""
    from database import SuperAdminPackage
    
    auth_check = require_superadmin(request)
    if auth_check:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    package = db.query(SuperAdminPackage).filter(SuperAdminPackage.id == package_id).first()
    if not package:
        return JSONResponse({"error": "Package not found"}, status_code=404)
    
    package_data = {
        "id": package.id,
        "package_name": package.package_name,
        "user_count": package.user_count,
        "package_type": package.package_type,
        "package_price": package.package_price,
        "description": package.description,
        "is_active": package.is_active
    }
    
    return JSONResponse({"package": package_data})

@app.post("/api/superadmin/packages")
async def create_superadmin_package_api(request: Request, db: Session = Depends(get_db)):
    """Create new package"""
    from database import SuperAdminPackage
    
    auth_check = require_superadmin(request)
    if auth_check:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        data = await request.json()
        
        # Validate required fields
        if not data.get("package_name"):
            return JSONResponse({"error": "Package name is required"}, status_code=400)
        if data.get("user_count") is None:
            return JSONResponse({"error": "User count is required"}, status_code=400)
        if not data.get("package_type"):
            return JSONResponse({"error": "Package type is required"}, status_code=400)
        if data.get("package_price") is None:
            return JSONResponse({"error": "Package price is required"}, status_code=400)
        
        existing = db.query(SuperAdminPackage).filter(
            SuperAdminPackage.package_name == data["package_name"]
        ).first()
        if existing:
            return JSONResponse({"error": "Package name already exists"}, status_code=400)
        
        new_package = SuperAdminPackage(
            package_name=data["package_name"],
            user_count=data["user_count"],
            package_type=data["package_type"],
            package_price=data["package_price"],
            description=data.get("description", ""),
            is_active=True
        )
        
        db.add(new_package)
        db.commit()
        db.refresh(new_package)
        
        return JSONResponse({
            "message": "Package created successfully",
            "package_id": new_package.id
        })
    
    except Exception as e:
        db.rollback()
        return JSONResponse({"error": str(e)}, status_code=500)

@app.put("/api/superadmin/packages/{package_id}")
async def update_superadmin_package_api(request: Request, package_id: int, db: Session = Depends(get_db)):
    """Update existing package"""
    from database import SuperAdminPackage
    
    auth_check = require_superadmin(request)
    if auth_check:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        package = db.query(SuperAdminPackage).filter(SuperAdminPackage.id == package_id).first()
        if not package:
            return JSONResponse({"error": "Package not found"}, status_code=404)
        
        data = await request.json()
        
        # Validate required fields
        if not data.get("package_name"):
            return JSONResponse({"error": "Package name is required"}, status_code=400)
        if data.get("user_count") is None:
            return JSONResponse({"error": "User count is required"}, status_code=400)
        if not data.get("package_type"):
            return JSONResponse({"error": "Package type is required"}, status_code=400)
        if data.get("package_price") is None:
            return JSONResponse({"error": "Package price is required"}, status_code=400)
        
        if data["package_name"] != package.package_name:
            existing = db.query(SuperAdminPackage).filter(
                SuperAdminPackage.package_name == data["package_name"],
                SuperAdminPackage.id != package_id
            ).first()
            if existing:
                return JSONResponse({"error": "Package name already exists"}, status_code=400)
        
        package.package_name = data["package_name"]
        package.user_count = data["user_count"]
        package.package_type = data["package_type"]
        package.package_price = data["package_price"]
        package.description = data.get("description", "")
        
        db.commit()
        
        return JSONResponse({"message": "Package updated successfully"})
    
    except Exception as e:
        db.rollback()
        return JSONResponse({"error": str(e)}, status_code=500)

@app.delete("/api/superadmin/packages/{package_id}")
async def delete_superadmin_package_api(request: Request, package_id: int, db: Session = Depends(get_db)):
    """Delete package (soft delete by setting is_active to False)"""
    from database import SuperAdminPackage
    
    auth_check = require_superadmin(request)
    if auth_check:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        package = db.query(SuperAdminPackage).filter(SuperAdminPackage.id == package_id).first()
        if not package:
            return JSONResponse({"error": "Package not found"}, status_code=404)
        
        package.is_active = False
        db.commit()
        
        return JSONResponse({"message": "Package deleted successfully"})
    
    except Exception as e:
        db.rollback()
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/superadmin/company-id/new")
async def generate_company_id_api(request: Request, db: Session = Depends(get_db)):
    from database import Company
    import random
    
    auth_check = require_superadmin(request)
    if auth_check:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    # Generate unique 8-digit company_id
    max_attempts = 100
    for _ in range(max_attempts):
        company_id = str(random.randint(10000000, 99999999))
        existing = db.query(Company).filter(Company.company_id == company_id).first()
        if not existing:
            return JSONResponse({"company_id": company_id})
    
    return JSONResponse({"error": "Failed to generate unique company ID"}, status_code=500)

@app.get("/api/superadmin/admins")
async def get_superadmin_admins_api(request: Request, db: Session = Depends(get_db)):
    from database import Company, Admin
    
    auth_check = require_superadmin(request)
    if auth_check:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    # DataTables parameters
    draw = request.query_params.get("draw", 1)
    start = int(request.query_params.get("start", 0))
    length = int(request.query_params.get("length", 10))
    
    # Get all admins with company info (exclude deleted/deactive companies)
    query = db.query(
        Company.company_id,
        Company.company_name,
        Company.package,
        Company.status,
        Company.end_date,
        Company.balance_amount,
        Admin.admin_name,
        Admin.admin_email,
        Admin.admin_mobile
    ).join(Admin, Company.company_id == Admin.company_id).filter(
        Company.status.in_(['Active', 'Suspended'])  # Exclude 'Deactive' (deleted) companies
    )
    
    total_records = query.count()
    
    # Pagination
    admins_query = query.offset(start).limit(length).all()
    
    data = []
    for row in admins_query:
        data.append({
            "company_id": row.company_id,
            "company_name": row.company_name,
            "admin_name": row.admin_name,
            "admin_email": row.admin_email,
            "admin_mobile": row.admin_mobile,
            "package": row.package or "Trial",
            "status": row.status or "Active",
            "end_date": row.end_date.isoformat() if row.end_date else None,
            "balance_amount": float(row.balance_amount) if row.balance_amount else 0.0
        })
    
    return JSONResponse({
        "draw": draw,
        "recordsTotal": total_records,
        "recordsFiltered": total_records,
        "data": data
    })

@app.post("/api/superadmin/admins")
async def create_superadmin_admin_api(request: Request, db: Session = Depends(get_db)):
    from database import Company, Admin
    from auth import get_password_hash
    from datetime import datetime
    from dateutil.relativedelta import relativedelta
    import random
    import os
    from pathlib import Path
    
    auth_check = require_superadmin(request)
    if auth_check:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        form_data = await request.form()
        
        company_id = form_data.get("company_id")
        if not company_id:
            while True:
                company_id = str(random.randint(10000000, 99999999))
                existing = db.query(Company).filter(Company.company_id == company_id).first()
                if not existing:
                    break
        else:
            existing = db.query(Company).filter(Company.company_id == company_id).first()
            if existing:
                return JSONResponse({
                    "success": False,
                    "detail": "Company ID already exists. Please regenerate."
                }, status_code=400)
        
        # Validate password confirmation
        password = form_data.get("password")
        confirm_password = form_data.get("confirm_password")
        if password != confirm_password:
            return JSONResponse({
                "success": False,
                "detail": "Passwords do not match"
            }, status_code=400)
        
        # Calculate end_date based on admin_type
        start_date_str = form_data.get("start_date")
        admin_type = form_data.get("admin_type", "Trial")
        period_months = int(form_data.get("period_months", 1))
        
        if start_date_str:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        else:
            start_date = datetime.now()
        
        if admin_type == "Trial":
            end_date = start_date + relativedelta(days=7)
        else:
            end_date = start_date + relativedelta(months=period_months)
        
        package_name = form_data.get("package", "Trial")
        from database import SuperAdminPackage
        package = db.query(SuperAdminPackage).filter(SuperAdminPackage.package_name == package_name).first()
        balance_amount = package.package_price if package else 0.0
        
        upload_dir = Path("/var/lib/autoispbilling/uploads") / company_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        
        logo_path = None
        profile_image_path = None
        qr_code_path = None
        
        company_logo = form_data.get("company_logo")
        if company_logo and hasattr(company_logo, 'filename') and company_logo.filename:
            if company_logo.size > 2 * 1024 * 1024:
                return JSONResponse({"success": False, "detail": "Company logo must be less than 2MB"}, status_code=400)
            logo_filename = f"logo_{company_id}{Path(company_logo.filename).suffix}"
            logo_path = str(upload_dir / logo_filename)
            with open(logo_path, "wb") as f:
                f.write(await company_logo.read())
        
        profile_image = form_data.get("profile_image")
        if profile_image and hasattr(profile_image, 'filename') and profile_image.filename:
            if profile_image.size > 2 * 1024 * 1024:
                return JSONResponse({"success": False, "detail": "Profile image must be less than 2MB"}, status_code=400)
            profile_filename = f"profile_{company_id}{Path(profile_image.filename).suffix}"
            profile_image_path = str(upload_dir / profile_filename)
            with open(profile_image_path, "wb") as f:
                f.write(await profile_image.read())
        
        qr_code = form_data.get("qr_code")
        if qr_code and hasattr(qr_code, 'filename') and qr_code.filename:
            if qr_code.size > 2 * 1024 * 1024:
                return JSONResponse({"success": False, "detail": "QR code must be less than 2MB"}, status_code=400)
            qr_filename = f"qr_{company_id}{Path(qr_code.filename).suffix}"
            qr_code_path = str(upload_dir / qr_filename)
            with open(qr_code_path, "wb") as f:
                f.write(await qr_code.read())
        
        # Create Company
        company = Company(
            company_id=company_id,
            company_name=form_data.get("company_name"),
            company_email=form_data.get("admin_email"),
            company_phone=form_data.get("admin_mobile"),
            company_address=form_data.get("company_address"),
            country=form_data.get("country", "India"),
            state=form_data.get("state"),
            city=form_data.get("city"),
            pincode=form_data.get("pincode"),
            gst_number=form_data.get("gst_number"),
            bank_name=form_data.get("bank_name"),
            account_number=form_data.get("account_number"),
            branch_code=form_data.get("branch_code"),
            branch_location=form_data.get("branch_location"),
            branch_ifsc=form_data.get("branch_ifsc"),
            upi_id=form_data.get("upi_id"),
            logo_path=logo_path,
            bank_qr_code=qr_code_path,
            declaration=form_data.get("declaration", ""),
            terms_conditions=form_data.get("terms_conditions", ""),
            package=package_name,
            admin_type=admin_type,
            period_months=period_months,
            start_date=start_date,
            end_date=end_date,
            status="Active",
            balance_amount=balance_amount
        )
        db.add(company)
        db.flush()
        
        # Generate admin_id (format: ADMIN001, ADMIN002, etc.)
        admin_count = db.query(Admin).filter(Admin.company_id == company_id).count()
        admin_id = f"ADMIN{str(admin_count + 1).zfill(3)}"
        
        # Create Admin
        admin = Admin(
            company_id=company_id,
            admin_id=admin_id,
            password_hash=get_password_hash(password),
            admin_name=form_data.get("admin_name"),
            admin_email=form_data.get("admin_email"),
            admin_mobile=form_data.get("admin_mobile"),
            profile_image_path=profile_image_path
        )
        db.add(admin)
        db.commit()
        
        return JSONResponse({
            "success": True,
            "message": "Admin created successfully",
            "company_id": company_id,
            "admin_id": admin_id
        })
        
    except Exception as e:
        db.rollback()
        import traceback
        return JSONResponse({
            "success": False,
            "detail": f"{str(e)}\n{traceback.format_exc()}"
        }, status_code=400)

@app.post("/api/superadmin/admins/{company_id}/toggle-status")
async def toggle_admin_status_api(company_id: str, request: Request, db: Session = Depends(get_db)):
    from database import Company
    
    auth_check = require_superadmin(request)
    if auth_check:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        body = await request.json()
        new_status = body.get("status")
        
        if new_status not in ["Active", "Deactive", "Suspended"]:
            return JSONResponse({
                "success": False,
                "message": "Invalid status"
            }, status_code=400)
        
        company = db.query(Company).filter(Company.company_id == company_id).first()
        if not company:
            return JSONResponse({
                "success": False,
                "message": "Company not found"
            }, status_code=404)
        
        company.status = new_status
        db.commit()
        
        return JSONResponse({
            "success": True,
            "message": f"Status updated to {new_status}",
            "status": new_status
        })
        
    except Exception as e:
        db.rollback()
        return JSONResponse({
            "success": False,
            "message": str(e)
        }, status_code=400)

@app.delete("/api/superadmin/admins/{company_id}")
async def delete_superadmin_admin_api(company_id: str, request: Request, db: Session = Depends(get_db)):
    from database import Company, Admin
    import logging
    
    auth_check = require_superadmin(request)
    if auth_check:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        logging.info(f"Attempting to delete admin with company_id: {company_id}")
        
        company = db.query(Company).filter(Company.company_id == company_id).first()
        if not company:
            logging.warning(f"Company not found: {company_id}")
            return JSONResponse({
                "success": False,
                "message": "Admin not found"
            }, status_code=404)
        
        # Soft delete by setting status to 'Deactive'
        company.status = 'Deactive'
        db.commit()
        
        logging.info(f"Successfully soft-deleted company: {company_id}")
        
        return JSONResponse({
            "success": True,
            "message": "Admin deleted successfully"
        })
        
    except Exception as e:
        db.rollback()
        logging.error(f"Error deleting admin {company_id}: {str(e)}")
        return JSONResponse({
            "success": False,
            "message": str(e)
        }, status_code=400)

@app.get("/superadmin/admins/add", response_class=HTMLResponse)
async def superadmin_add_admin(request: Request, db: Session = Depends(get_db)):
    auth_check = require_superadmin(request)
    if auth_check:
        return auth_check
    context = get_superadmin_context(request, db)
    context["active_page"] = "admin_management"
    return templates.TemplateResponse("superadmin_add_admin.html", context)

@app.get("/superadmin/packages", response_class=HTMLResponse)
async def superadmin_packages(request: Request, db: Session = Depends(get_db)):
    auth_check = require_superadmin(request)
    if auth_check:
        return auth_check
    context = get_superadmin_context(request, db)
    context["active_page"] = "packages"
    return templates.TemplateResponse("superadmin_packages.html", context)

# Stub routes for remaining 10 menu pages
@app.get("/superadmin/transactions", response_class=HTMLResponse)
async def superadmin_transactions(request: Request, db: Session = Depends(get_db)):
    auth_check = require_superadmin(request)
    if auth_check:
        return auth_check
    context = get_superadmin_context(request, db)
    context["active_page"] = "transactions"
    return templates.TemplateResponse("superadmin_transactions.html", context)

@app.get("/superadmin/complaints", response_class=HTMLResponse)
async def superadmin_complaints(request: Request, db: Session = Depends(get_db)):
    auth_check = require_superadmin(request)
    if auth_check:
        return auth_check
    context = get_superadmin_context(request, db)
    context["active_page"] = "complaints"
    return templates.TemplateResponse("superadmin_complaints.html", context)

@app.get("/superadmin/support", response_class=HTMLResponse)
async def superadmin_support(request: Request, db: Session = Depends(get_db)):
    auth_check = require_superadmin(request)
    if auth_check:
        return auth_check
    context = get_superadmin_context(request, db)
    context["active_page"] = "support"
    return templates.TemplateResponse("superadmin_support.html", context)

@app.get("/superadmin/notifications", response_class=HTMLResponse)
async def superadmin_notifications(request: Request, db: Session = Depends(get_db)):
    auth_check = require_superadmin(request)
    if auth_check:
        return auth_check
    context = get_superadmin_context(request, db)
    context["active_page"] = "notifications"
    return templates.TemplateResponse("superadmin_notifications.html", context)

@app.get("/superadmin/whatsapp-campaign", response_class=HTMLResponse)
async def superadmin_whatsapp_campaign(request: Request, db: Session = Depends(get_db)):
    auth_check = require_superadmin(request)
    if auth_check:
        return auth_check
    context = get_superadmin_context(request, db)
    context["active_page"] = "whatsapp_campaign"
    return templates.TemplateResponse("superadmin_whatsapp_campaign.html", context)

@app.get("/superadmin/whatsapp-templates", response_class=HTMLResponse)
async def superadmin_whatsapp_templates(request: Request, db: Session = Depends(get_db)):
    auth_check = require_superadmin(request)
    if auth_check:
        return auth_check
    context = get_superadmin_context(request, db)
    context["active_page"] = "whatsapp_templates"
    return templates.TemplateResponse("superadmin_whatsapp_templates.html", context)

@app.get("/superadmin/reports", response_class=HTMLResponse)
async def superadmin_reports(request: Request, db: Session = Depends(get_db)):
    auth_check = require_superadmin(request)
    if auth_check:
        return auth_check
    context = get_superadmin_context(request, db)
    context["active_page"] = "reports"
    return templates.TemplateResponse("superadmin_reports.html", context)

@app.get("/superadmin/sent-invoices", response_class=HTMLResponse)
async def superadmin_sent_invoices(request: Request, db: Session = Depends(get_db)):
    auth_check = require_superadmin(request)
    if auth_check:
        return auth_check
    context = get_superadmin_context(request, db)
    context["active_page"] = "sent_invoices"
    return templates.TemplateResponse("superadmin_sent_invoices.html", context)

@app.get("/superadmin/deleted-admins", response_class=HTMLResponse)
async def superadmin_deleted_admins(request: Request, db: Session = Depends(get_db)):
    auth_check = require_superadmin(request)
    if auth_check:
        return auth_check
    context = get_superadmin_context(request, db)
    context["active_page"] = "deleted_admins"
    return templates.TemplateResponse("superadmin_deleted_admins.html", context)

@app.get("/superadmin/sms-logs", response_class=HTMLResponse)
async def superadmin_sms_logs(request: Request, db: Session = Depends(get_db)):
    auth_check = require_superadmin(request)
    if auth_check:
        return auth_check
    context = get_superadmin_context(request, db)
    context["active_page"] = "sms_logs"
    return templates.TemplateResponse("superadmin_sms_logs.html", context)

# Superadmin Admin Detail and Management Endpoints
@app.get("/api/superadmin/admins/{company_id}/details")
async def get_superadmin_admin_details_api(company_id: str, request: Request, db: Session = Depends(get_db)):
    """Get detailed information about an admin for View modal"""
    from database import Company, Admin, Customer
    
    auth_check = require_superadmin(request)
    if auth_check:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        # Get company and admin info
        company = db.query(Company).filter(Company.company_id == company_id).first()
        if not company:
            return JSONResponse({"error": "Admin not found"}, status_code=404)
        
        admin = db.query(Admin).filter(Admin.company_id == company_id).first()
        if not admin:
            return JSONResponse({"error": "Admin not found"}, status_code=404)
        
        # Count total users created by this admin
        total_users = db.query(Customer).filter(Customer.company_id == company_id).count()
        
        # Prepare response data
        details = {
            "company_id": company.company_id,
            "company_name": company.company_name,
            "company_email": company.company_email,
            "company_phone": company.company_phone,
            "company_address": company.company_address or "",
            "city": company.city or "",
            "state": company.state or "",
            "country": company.country or "",
            "pincode": company.pincode or "",
            "admin_name": admin.admin_name,
            "admin_email": admin.admin_email,
            "admin_mobile": admin.admin_mobile or "",
            "admin_id": admin.admin_id,
            "package": company.package or "Trial",
            "admin_type": company.admin_type or "Trial",
            "start_date": company.start_date.isoformat() if company.start_date else None,
            "end_date": company.end_date.isoformat() if company.end_date else None,
            "status": company.status or "Active",
            "balance_amount": float(company.balance_amount) if company.balance_amount else 0.0,
            "total_users": total_users,
            "password_note": "Password is hashed and cannot be displayed. Use 'Reset Password' to generate a new one."
        }
        
        return JSONResponse({"details": details})
        
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/superadmin/admins/{company_id}")
async def get_superadmin_admin_api(company_id: str, request: Request, db: Session = Depends(get_db)):
    """Get admin data for Edit modal"""
    from database import Company, Admin
    
    auth_check = require_superadmin(request)
    if auth_check:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        company = db.query(Company).filter(Company.company_id == company_id).first()
        if not company:
            return JSONResponse({"error": "Admin not found"}, status_code=404)
        
        admin = db.query(Admin).filter(Admin.company_id == company_id).first()
        if not admin:
            return JSONResponse({"error": "Admin not found"}, status_code=404)
        
        admin_data = {
            "company_id": company.company_id,
            "company_name": company.company_name,
            "company_email": company.company_email,
            "company_phone": company.company_phone,
            "company_address": company.company_address or "",
            "country": company.country or "",
            "state": company.state or "",
            "city": company.city or "",
            "pincode": company.pincode or "",
            "gst_number": company.gst_number or "",
            "bank_name": company.bank_name or "",
            "account_number": company.account_number or "",
            "branch_code": company.branch_code or "",
            "branch_location": company.branch_location or "",
            "branch_ifsc": company.branch_ifsc or "",
            "upi_id": company.upi_id or "",
            "declaration": company.declaration or "",
            "terms_conditions": company.terms_conditions or "",
            "admin_name": admin.admin_name,
            "admin_email": admin.admin_email,
            "admin_mobile": admin.admin_mobile or "",
            "admin_id": admin.admin_id,
            "package": company.package or "Trial",
            "admin_type": company.admin_type or "Trial",
            "period_months": company.period_months or 1,
            "start_date": company.start_date.isoformat() if company.start_date else None,
            "end_date": company.end_date.isoformat() if company.end_date else None,
            "status": company.status or "Active"
        }
        
        return JSONResponse({"admin": admin_data})
        
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.put("/api/superadmin/admins/{company_id}")
async def update_superadmin_admin_api(company_id: str, request: Request, db: Session = Depends(get_db)):
    """Update admin data from Edit modal"""
    from database import Company, Admin
    from auth import get_password_hash
    from datetime import datetime
    from dateutil.relativedelta import relativedelta
    
    auth_check = require_superadmin(request)
    if auth_check:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        form_data = await request.form()
        
        company = db.query(Company).filter(Company.company_id == company_id).first()
        if not company:
            return JSONResponse({"error": "Admin not found"}, status_code=404)
        
        admin = db.query(Admin).filter(Admin.company_id == company_id).first()
        if not admin:
            return JSONResponse({"error": "Admin not found"}, status_code=404)
        
        # Update company fields
        company.company_name = form_data.get("company_name")
        company.company_email = form_data.get("company_email")
        company.company_phone = form_data.get("company_phone")
        company.company_address = form_data.get("company_address", "")
        company.country = form_data.get("country", "")
        company.state = form_data.get("state", "")
        company.city = form_data.get("city", "")
        company.pincode = form_data.get("pincode", "")
        company.gst_number = form_data.get("gst_number", "")
        company.bank_name = form_data.get("bank_name", "")
        company.account_number = form_data.get("account_number", "")
        company.branch_code = form_data.get("branch_code", "")
        company.branch_location = form_data.get("branch_location", "")
        company.branch_ifsc = form_data.get("branch_ifsc", "")
        company.upi_id = form_data.get("upi_id", "")
        company.declaration = form_data.get("declaration", "")
        company.terms_conditions = form_data.get("terms_conditions", "")
        company.package = form_data.get("package", "Trial")
        company.admin_type = form_data.get("admin_type", "Trial")
        company.period_months = int(form_data.get("period_months", 1))
        
        # Update admin fields
        admin.admin_name = form_data.get("admin_name")
        admin.admin_email = form_data.get("admin_email")
        admin.admin_mobile = form_data.get("admin_mobile", "")
        
        # Update password if provided
        password = form_data.get("password")
        if password:
            admin.password_hash = get_password_hash(password)
        
        # Recalculate dates if admin_type or period changed
        admin_type = form_data.get("admin_type", "Trial")
        if admin_type == "Trial":
            company.start_date = datetime.now()
            company.end_date = datetime.now() + relativedelta(days=7)
        else:
            period_months = int(form_data.get("period_months", 1))
            company.start_date = datetime.now()
            company.end_date = datetime.now() + relativedelta(months=period_months)
        
        db.commit()
        
        return JSONResponse({
            "success": True,
            "message": "Admin updated successfully"
        })
        
    except Exception as e:
        db.rollback()
        return JSONResponse({
            "success": False,
            "message": str(e)
        }, status_code=400)

@app.post("/api/superadmin/admins/{company_id}/payments")
async def add_superadmin_admin_payment_api(company_id: str, request: Request, db: Session = Depends(get_db)):
    """Add payment against admin's due balance"""
    from database import Company, Transaction
    from datetime import datetime
    
    auth_check = require_superadmin(request)
    if auth_check:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        data = await request.json()
        
        company = db.query(Company).filter(Company.company_id == company_id).first()
        if not company:
            return JSONResponse({"error": "Admin not found"}, status_code=404)
        
        amount = float(data.get("amount", 0))
        payment_date = data.get("payment_date")
        payment_method = data.get("payment_method", "Cash")
        reference_no = data.get("reference_no", "")
        note = data.get("note", "")
        
        if amount <= 0:
            return JSONResponse({
                "success": False,
                "message": "Amount must be greater than 0"
            }, status_code=400)
        
        # Parse payment date
        if payment_date:
            payment_date = datetime.fromisoformat(payment_date.replace('Z', '+00:00'))
        else:
            payment_date = datetime.now()
        
        # Create transaction record
        transaction = Transaction(
            company_id=company_id,
            customer_id=None,  # This is an admin payment, not customer payment
            amount=amount,
            payment_method=payment_method,
            reference_no=reference_no,
            note=f"Admin payment: {note}",
            transaction_date=payment_date,
            created_at=datetime.now()
        )
        db.add(transaction)
        
        # Update company balance (reduce due amount)
        current_balance = company.balance_amount or 0.0
        company.balance_amount = current_balance - amount
        
        db.commit()
        
        return JSONResponse({
            "success": True,
            "message": "Payment added successfully",
            "new_balance": float(company.balance_amount)
        })
        
    except Exception as e:
        db.rollback()
        return JSONResponse({
            "success": False,
            "message": str(e)
        }, status_code=400)

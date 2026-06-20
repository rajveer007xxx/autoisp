from passlib.context import CryptContext
from sqlalchemy.orm import Session
from database import Admin, Employee, Customer, Company, SuperAdmin

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def authenticate_superadmin(db: Session, superadmin_id: str, password: str):
    superadmin = db.query(SuperAdmin).filter(SuperAdmin.superadmin_id == superadmin_id).first()
    if not superadmin:
        return None
    if not verify_password(password, superadmin.password_hash):
        return None
    return superadmin

def authenticate_admin(db: Session, company_id: str, admin_id: str, password: str):
    admin = db.query(Admin).filter(Admin.admin_id == admin_id, Admin.company_id == company_id).first()
    if not admin:
        return None
    if not verify_password(password, admin.password_hash):
        return None
    return admin

def authenticate_employee(db: Session, company_id: str, employee_code: str, password: str):
    employee = db.query(Employee).filter(
        Employee.employee_code == employee_code, 
        Employee.company_id == company_id,
        Employee.is_deleted == False
    ).first()
    if not employee:
        return None
    if not verify_password(password, employee.password_hash):
        return None
    if hasattr(employee, 'is_active') and not employee.is_active:
        return None
    return employee

def authenticate_customer(db: Session, customer_id: str, password: str):
    # Session 19 — allow customers to log into the User Portal using
    # either their Customer-ID OR the PPPoE username (the same creds
    # entered on Add-Customer now power BOTH the router dial-in AND
    # the web portal login). Password still verified against the
    # bcrypt-hashed password_hash column.
    customer = db.query(Customer).filter(Customer.customer_id == customer_id).first()
    if not customer:
        customer = db.query(Customer).filter(Customer.username == customer_id).first()
    if not customer:
        return None
    if not verify_password(password, customer.password_hash):
        return None
    return customer

def seed_database(db: Session):
    if db.query(Company).first():
        return
    
    company = Company(
        company_id="12345678",
        company_name="Test ISP Company",
        company_email="admin@testispcompany.com",
        company_phone="+918085868114"
    )
    db.add(company)
    
    admin = Admin(
        admin_id="ADMIN001",
        password_hash=get_password_hash("Admin@123"),
        admin_name="Admin User",
        admin_email="admin@testispcompany.com",
        company_id="12345678"
    )
    db.add(admin)
    
    employee = Employee(
        employee_code="TES00000001",
        password_hash=get_password_hash("Employee@123"),
        employee_name="Employee User",
        email="employee@testispcompany.com",
        mobile="9876543210",
        company_id="12345678"
    )
    db.add(employee)
    
    customer = Customer(
        customer_id="CUST001",
        password_hash=get_password_hash("Customer@123"),
        customer_name="Customer User",
        customer_email="customer@example.com",
        customer_phone="+919876543210"
    )
    db.add(customer)
    
    db.commit()

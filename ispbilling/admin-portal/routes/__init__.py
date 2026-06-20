"""Future-home for extracted route modules.

Convention: one FastAPI APIRouter per file (e.g. vouchers.py, nas.py,
customers.py).  Include them in main.py via:

    from routes import vouchers
    app.include_router(vouchers.router)

Keep each router <500 lines. Main.py becomes the composition root.
"""

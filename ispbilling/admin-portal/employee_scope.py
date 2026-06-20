"""Employee location scoping (mirrors sub_lco._SUB_LCO_CTX).

Setup mirrors sub_lco.py: registers the `do_orm_execute` listener on
SQLAlchemy's `Session` (not on the engine — that's the bug we hit
earlier). When `user_type == "employee"` is hitting any admin-portal
endpoint that runs an ORM `Customer` query, this module:

  1. Sets a ContextVar with the EXACT-MATCH list of `Location.name`
     values assigned to that employee (via `EmployeeLocalityAssignment`).
  2. Auto-injects `UPPER(TRIM(customers.locality)) IN (<names>)` into
     every Customer SELECT.

Empty list -> impossible filter (locality = '__NONE__') so a misconfigured
employee sees nothing rather than the whole tenant.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Tuple, Optional

from sqlalchemy import event, text as _text
from sqlalchemy.orm import Session as OrmSession

_EMP_LOC_CTX: ContextVar[Optional[Tuple[str, ...]]] = ContextVar(
    "employee_loc_scope", default=None,
)
# __S39K__ Sub-LCO ownership scope. Sentinel object means "not set",
# None means employee belongs to admin (sub_lco_id IS NULL),
# int means employee belongs to that sub_lco.
_UNSET = object()
_EMP_SLCO_CTX: ContextVar = ContextVar("employee_slco_scope", default=_UNSET)
# __S43ZB__ track employee PK so scope filter can OR-in created_by_employee_id
_EMP_ID_CTX: ContextVar = ContextVar("employee_id_scope", default=None)
_LOC_CACHE: dict = {}
_ATTACHED = False


def resolve_locations(db, company_id: str, employee_id: int) -> Tuple[str, ...]:
    if not employee_id or not company_id:
        return ()
    key = (str(company_id), int(employee_id))
    cached = _LOC_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        from database import EmployeeLocalityAssignment, Location
        rows = (db.query(Location.name)
                  .join(EmployeeLocalityAssignment,
                        EmployeeLocalityAssignment.location_id == Location.id)
                  .filter(EmployeeLocalityAssignment.employee_id == employee_id,
                          EmployeeLocalityAssignment.company_id == company_id,
                          EmployeeLocalityAssignment.active == True)  # noqa: E712
                  .all())
        names = tuple(sorted({(r[0] or "").strip().upper()
                              for r in rows if (r[0] or "").strip()}))
        _LOC_CACHE[key] = names
        return names
    except Exception as e:  # noqa: BLE001
        print(f"[employee_scope] resolve_locations failed: {e}")
        return ()


def _sub_lco_emp_ids(db, company_id, sub_lco_id):
    """__S43ZG__ Return PKs of employees whose sub_lco_id == this sub_lco_id."""
    try:
        rows = db.execute(_text(
            "SELECT id FROM employees WHERE company_id=:c AND sub_lco_id=:s"
        ), {"c": company_id, "s": int(sub_lco_id)}).fetchall()
        return [int(r[0]) for r in rows if r[0] is not None]
    except Exception:
        return []


def set_scope_from_session(request, db) -> None:
    if (request.session.get("user_type") or "").lower() != "employee":
        return
    company_id = request.session.get("company_id") or ""
    employee_id = request.session.get("employee_id") or 0
    if not company_id or not employee_id:
        _EMP_LOC_CTX.set(())
        _EMP_SLCO_CTX.set(None)
        return
    _EMP_LOC_CTX.set(resolve_locations(db, company_id, int(employee_id)))
    _EMP_ID_CTX.set(int(employee_id))
    # __S39K__ resolve sub_lco_id of the employee
    try:
        from database import Employee as _Emp
        emp = db.query(_Emp.sub_lco_id).filter(
            _Emp.id == int(employee_id),
            _Emp.company_id == company_id,
        ).first()
        _EMP_SLCO_CTX.set(int(emp[0]) if emp and emp[0] is not None else None)
    except Exception as _ex:
        print(f"[employee_scope] sub_lco resolve failed: {_ex}")
        _EMP_SLCO_CTX.set(None)


def invalidate_cache(employee_id: int) -> None:
    keys = [k for k in _LOC_CACHE if k[1] == int(employee_id)]
    for k in keys:
        _LOC_CACHE.pop(k, None)


def attach_scope_events(_engine_unused=None) -> None:
    """Idempotent. The `_engine_unused` argument is accepted only so
    callers can use the same call-shape as sub_lco's wiring."""
    global _ATTACHED
    if _ATTACHED:
        return
    _ATTACHED = True

    from database import Customer  # noqa: F401 — verifies model importable

    @event.listens_for(OrmSession, "do_orm_execute")
    def _scope_employee(orm_execute_state):
        names = _EMP_LOC_CTX.get()
        if names is None:
            return                                 # not an employee path
        if not orm_execute_state.is_select:
            return
        try:
            ents = orm_execute_state.bind_arguments.get("_orm_load_options", None)
            try:
                ents = orm_execute_state.statement.column_descriptions
            except Exception:
                ents = []
        except Exception:
            ents = []

        # Identify Customer entities in the statement.
        from database import Customer as _C
        targets = []
        try:
            for desc in (ents or []):
                if desc.get("type") is _C or desc.get("entity") is _C:
                    targets.append("customers.locality")
                    break
        except Exception:
            pass
        # Fallback heuristic — match plain `db.query(Customer)` calls.
        if not targets:
            try:
                from sqlalchemy.orm.context import _ColumnEntity  # noqa: F401
            except Exception:
                pass
            try:
                cls_set = set()
                for m in orm_execute_state.statement.get_final_froms():
                    if hasattr(m, "name") and m.name == "customers":
                        cls_set.add("customers")
                if cls_set:
                    targets.append("customers.locality")
            except Exception:
                pass
        if not targets:
            return
        try:
            stmt = orm_execute_state.statement
            # __S43ZB__ Strict isolation per diagram (hybrid option C):
            #   (created_by_employee_id = self) OR
            #   (sub_lco_id = parent_slco AND UPPER(locality) IN (names))
            eid = _EMP_ID_CTX.get()
            slc = _EMP_SLCO_CTX.get()
            own = f"customers.created_by_employee_id = {int(eid)} OR " if eid else ""
            if not names:
                if eid:
                    stmt = stmt.where(_text(f"customers.created_by_employee_id = {int(eid)}"))
                else:
                    stmt = stmt.where(_text("1=0"))
            else:
                quoted = ",".join("'" + n.replace("'", "''") + "'" for n in names)
                if slc is None:
                    stmt = stmt.where(_text(
                        f"({own}(customers.sub_lco_id IS NULL "
                        f" AND UPPER(TRIM(customers.locality)) IN ({quoted})))"))
                elif slc is _UNSET:
                    stmt = stmt.where(_text(
                        f"({own}UPPER(TRIM(customers.locality)) IN ({quoted}))"))
                else:
                    stmt = stmt.where(_text(
                        f"({own}(customers.sub_lco_id = {int(slc)} "
                        f" AND UPPER(TRIM(customers.locality)) IN ({quoted})))"))
            orm_execute_state.statement = stmt
        except Exception as e:
            print(f"[employee_scope] filter failed: {e}")

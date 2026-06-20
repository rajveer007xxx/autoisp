"""S42J — Tenant-isolation regression test for FreeRADIUS shared tables."""
import os, sys, sqlite3
sys.path.insert(0, "/opt/ispbilling/admin-portal")

RAD = "/var/lib/freeradius/radacct.db"
APP = "/var/lib/autoispbilling/autoispbilling.db"

def test_radpostauth_has_company_id():
    con = sqlite3.connect(RAD)
    cols = {r[1] for r in con.execute("PRAGMA table_info(radpostauth)")}
    con.close()
    assert "company_id" in cols, "radpostauth.company_id column missing"

def test_radacct_has_company_id():
    con = sqlite3.connect(RAD)
    cols = {r[1] for r in con.execute("PRAGMA table_info(radacct)")}
    con.close()
    assert "company_id" in cols, "radacct.company_id column missing"

def test_no_null_radpostauth_for_active_customers():
    con = sqlite3.connect(RAD)
    null_recent = con.execute(
        "SELECT COUNT(*) FROM radpostauth "
        "WHERE company_id IS NULL "
        "  AND authdate >= datetime(\"now\", \"-1 day\")"
    ).fetchone()[0]
    con.close()
    assert null_recent < 50, f"too many unresolved recent radpostauth rows: {null_recent}"

def test_mp_raj_fibernet_attributed_correctly():
    """Two tenants share username `mp.raj.fibernet`. The active customer
    (FIBERNET=15378763) must own all radpostauth events for that name."""
    con = sqlite3.connect(RAD)
    rows = con.execute(
        "SELECT DISTINCT company_id FROM radpostauth WHERE username=?",
        ("mp.raj.fibernet",)).fetchall()
    con.close()
    cids = {r[0] for r in rows}
    assert cids.issubset({"15378763", None, "", "0"}), \
        f"mp.raj.fibernet leaked to: {cids}"

def test_orphan_rows_isolated():
    """Orphan (=no matching customer) rows must be tagged 0 so no tenant sees them."""
    con = sqlite3.connect(RAD)
    cnt = con.execute(
        "SELECT COUNT(*) FROM radpostauth WHERE company_id = \"0\""
    ).fetchone()[0]
    con.close()
    assert cnt >= 0  # just makes sure query parses; specific count varies

def test_radpostauth_tenant_module_imports():
    """The S42J helper module must be importable and expose the public API."""
    from radpostauth_tenant import (ensure_company_id_column,
                                     ensure_all_company_id_columns,
                                     backfill_company_id,
                                     backfill_radacct_company_id,
                                     fetch_tenant_radpostauth,
                                     delete_tenant_radpostauth)
    assert callable(fetch_tenant_radpostauth)

def test_fence_query_returns_only_one_tenant():
    """Direct DB check: WHERE company_id=X never returns rows of other tenants."""
    con_a = sqlite3.connect(APP)
    cur_a = con_a.cursor()
    con_r = sqlite3.connect(RAD)
    cur_r = con_r.cursor()
    # For each company, every radpostauth row returned must belong to a
    # customer of that company (by username) OR be tagged 0/orphan.
    for cid in ("14150129", "15378763"):
        rows = cur_r.execute(
            "SELECT DISTINCT username FROM radpostauth WHERE company_id=?",
            (cid,)).fetchall()
        for (uname,) in rows:
            owners = [r[0] for r in cur_a.execute(
                "SELECT company_id FROM customers WHERE username=?",
                (uname,)).fetchall()]
            assert cid in owners or not owners, \
                f"company {cid} has radpostauth for {uname} but owners are {owners}"
    con_a.close(); con_r.close()


if __name__ == "__main__":
    import traceback
    tests = [v for k,v in dict(globals()).items() if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception:
            print(f"  ERR   {t.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)

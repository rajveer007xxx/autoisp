#!/usr/bin/env python3
"""
_S58AW_ NAS Alert Watcher

Polls every active NAS device per tenant, reads MikroTik /system/health
(temperature, PSU1/PSU2 state) via librouteros, and fires a Telegram
alert when:
  • PSU2-state == "lost" (Dual-PSU box dropped to Single-PSU), OR
  • temperature_c > 70 (configurable via NAS_TEMP_C_THRESHOLD env)

Alert dedup: 60-minute cooldown per (nas_id, alert_kind) — state file is
/var/lib/ispbilling/nas_alert_state.json.

Telegram config resolution order (first hit wins):
  1) companies.telegram_bot_token + companies.telegram_admin_chat_id
  2) /etc/ispbilling.env  → TELEGRAM_BOT_TOKEN + TELEGRAM_ADMIN_CHAT_ID

Designed to be invoked every 60 s by a systemd timer (or cron). Exits 0
even on per-NAS failures — those are logged and reported in stdout.
"""
import os, sys, json, time, traceback
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any

# ─── Config ─────────────────────────────────────────────────────────
TEMP_C_THRESHOLD = float(os.environ.get("NAS_TEMP_C_THRESHOLD", "70"))
COOLDOWN_SECONDS = int(os.environ.get("NAS_ALERT_COOLDOWN", str(3600)))
STATE_FILE = Path("/var/lib/ispbilling/nas_alert_state.json")
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

# Pull /etc/ispbilling.env for SQLAlchemy + Telegram defaults.
ENV_FILE = "/etc/ispbilling.env"
if Path(ENV_FILE).exists():
    for line in Path(ENV_FILE).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DEFAULT_TG_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
DEFAULT_TG_CHATID = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")

if not DATABASE_URL:
    print("[nas-alert] no DATABASE_URL set, aborting.")
    sys.exit(0)


# ─── State helpers ─────────────────────────────────────────────────
def _state_load() -> Dict[str, float]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text() or "{}")
    except Exception:
        return {}

def _state_save(state: Dict[str, float]):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        print(f"[nas-alert] state save failed: {e}")

def _should_alert(state: dict, key: str) -> bool:
    last = float(state.get(key) or 0)
    return (time.time() - last) >= COOLDOWN_SECONDS


# ─── Telegram send ─────────────────────────────────────────────────
def _telegram_send(token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id:
        return False
    import urllib.request, urllib.parse, json as _j
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=8) as r:
            resp = _j.loads(r.read().decode() or "{}")
            return bool(resp.get("ok"))
    except Exception as e:
        print(f"[nas-alert] telegram send failed: {e}")
        return False


# ─── MikroTik health probe ─────────────────────────────────────────
def _mk_probe(host: str, user: str, password: str, port: int,
              use_tls: bool) -> Optional[Dict[str, Any]]:
    """Tries librouteros first, then falls back to silent failure."""
    try:
        from librouteros import connect
        from librouteros.login import plain
    except Exception as e:
        print(f"[nas-alert] librouteros unavailable: {e}")
        return None
    try:
        kwargs = dict(host=host, username=user, password=password,
                      port=int(port or 8728), timeout=4,
                      login_method=plain)
        if use_tls:
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            kwargs["ssl_wrapper"] = lambda s: ctx.wrap_socket(s)
        api = connect(**kwargs)
    except Exception as e:
        return {"_error": str(e)}
    out: Dict[str, Any] = {}  # _S58AY_PROBE_FIX_  use .path() syntax
    try:
        health = list(api.path("/system/health"))
        for r in health:
            nm = (r.get("name") or "").lower()
            val = r.get("value")
            try: f = float(val) if val not in (None, "") else None
            except Exception: f = None
            if nm == "temperature" and f is not None:
                out["temperature_c"] = f
            elif nm == "cpu-temperature" and f is not None:
                out["cpu_temperature_c"] = f
            elif nm in ("voltage", "psu1-voltage") and f is not None:
                out["voltage_v"] = f
            elif nm == "psu1-state":
                out["psu1_state"] = str(val or "").lower() or None
            elif nm == "psu2-state":
                out["psu2_state"] = str(val or "").lower() or None
        # Fallback: v6 format — flat keys on the first row.
        if not out and health:
            hf = health[0]
            if "temperature" in hf:
                try: out["temperature_c"] = float(hf["temperature"])
                except Exception: pass
            if "psu1-state" in hf:
                out["psu1_state"] = str(hf.get("psu1-state") or "").lower() or None
            if "psu2-state" in hf:
                out["psu2_state"] = str(hf.get("psu2-state") or "").lower() or None
    except Exception as e:
        out["_health_error"] = str(e)
        print(f"[nas-alert probe] {host} /system/health failed: {e}")
    try:
        api.close()
    except Exception:
        pass
    return out


# ─── Main loop ─────────────────────────────────────────────────────
def main():
    from sqlalchemy import create_engine, text
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
    state = _state_load()

    # Detect optional telegram_* columns once.
    with engine.connect() as cn:
        has_tg_cols = bool(cn.execute(text(
            "SELECT 1 FROM information_schema.columns "
            " WHERE table_name='companies' AND column_name='telegram_bot_token' LIMIT 1"
        )).fetchone())

    select_cols = ("id, name, ip_address, IFNULL(port,8728) AS port, "
                   "IFNULL(api_username,'admin') AS api_username, "
                   "IFNULL(api_password,'') AS api_password, "
                   "IFNULL(use_tls,0) AS use_tls, company_id")
    # Postgres has no IFNULL → use COALESCE.
    if DATABASE_URL.startswith(("postgres", "postgresql")):
        select_cols = ("id, name, ip_address, COALESCE(port,8728) AS port, "
                       "COALESCE(api_username,'admin') AS api_username, "
                       "COALESCE(api_password,'') AS api_password, "
                       "COALESCE(use_tls,0) AS use_tls, company_id")

    with engine.connect() as cn:
        rows = cn.execute(text(
            f"SELECT {select_cols} FROM nas_devices "
            "  WHERE status='Active'"
        )).fetchall()

    if not rows:
        print(f"[nas-alert] no active NAS devices to check")
        return

    # Resolve company-scoped Telegram config in one query.
    tg_by_cid: Dict[str, Dict[str, str]] = {}
    if has_tg_cols:
        with engine.connect() as cn:
            for r in cn.execute(text(
                "SELECT company_id, telegram_bot_token, telegram_admin_chat_id "
                "  FROM companies "
                " WHERE COALESCE(telegram_bot_token,'') <> '' "
                "   AND COALESCE(telegram_admin_chat_id,'') <> ''"
            )).fetchall():
                tg_by_cid[r[0]] = {"token": r[1] or "", "chat": r[2] or ""}

    fired = 0
    for r in rows:
        nas_id = r[0]; nas_name = r[1] or f"NAS#{nas_id}"
        host = r[2]; port = r[3]
        user = r[4]; pw = r[5]; tls = bool(r[6])
        cid = r[7] or ""
        try:
            h = _mk_probe(host, user, pw, port, tls) or {}
            temp = h.get("temperature_c")
            psu1 = (h.get("psu1_state") or "").lower()
            psu2 = (h.get("psu2_state") or "").lower()

            alerts = []
            if temp is not None and temp > TEMP_C_THRESHOLD:
                alerts.append(("temp_high",
                    f"🔥 <b>{nas_name}</b> ({host}) temperature is "
                    f"<b>{temp:.1f} °C</b> — exceeds threshold "
                    f"of {TEMP_C_THRESHOLD:.0f} °C."))
            # "lost" / "fail" on PSU2 indicates Single-PSU mode on a dual-PSU box.
            if psu2 and psu2 in ("lost", "fail", "failed", "absent"):
                alerts.append(("psu_single",
                    f"⚡ <b>{nas_name}</b> ({host}) is running on a "
                    f"<b>Single PSU</b> — PSU2 state = <code>{psu2}</code>. "
                    f"Investigate before the remaining PSU fails."))
            elif psu1 and psu1 in ("lost", "fail", "failed", "absent"):
                alerts.append(("psu_single",
                    f"⚡ <b>{nas_name}</b> ({host}): PSU1 has dropped — "
                    f"state = <code>{psu1}</code>."))

            for kind, msg in alerts:
                key = f"{nas_id}:{kind}"
                if not _should_alert(state, key):
                    continue
                tg = tg_by_cid.get(cid, {})
                token = tg.get("token") or DEFAULT_TG_TOKEN
                chat  = tg.get("chat")  or DEFAULT_TG_CHATID
                if not token or not chat:
                    print(f"[nas-alert] {nas_name}: alert '{kind}' suppressed "
                          f"(no Telegram config for tenant {cid})")
                    continue
                ok = _telegram_send(token, chat,
                    f"{msg}\n\n<i>Time: "
                    f"{datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}</i>")
                if ok:
                    state[key] = time.time()
                    fired += 1
                    print(f"[nas-alert] ✓ alert sent: {key}")
        except Exception as e:
            print(f"[nas-alert] {nas_name} probe error: {e}")
            traceback.print_exc()

    _state_save(state)
    print(f"[nas-alert] done — {len(rows)} NAS checked, {fired} alert(s) fired")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[nas-alert] fatal: {e}")
        traceback.print_exc()
        sys.exit(0)  # never fail-hard the systemd timer

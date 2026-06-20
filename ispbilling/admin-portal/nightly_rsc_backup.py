#!/opt/ispbilling/venv/bin/python
"""Nightly `.rsc` backup for every registered, Active NAS Device.

Runs under systemd-timer (see /etc/systemd/system/isp-rsc-backup.{service,timer}).
For each NAS: opens a RouterOS API connection, runs `/export` (the flat text
dump), and writes the output to:
    /opt/ispbilling/rsc_backups/{company_id}/{nas_name}/{YYYY-MM-DD}.rsc

Retains the last 30 days per NAS (older backups are deleted). Errors per-NAS
are logged but don't stop the whole run.
"""
import datetime
import os
import shutil
import sys
import traceback

sys.path.insert(0, "/opt/ispbilling/admin-portal")

from database import SessionLocal
import radius_network as rn
import routeros_provision as rp

BACKUP_ROOT = "/opt/ispbilling/rsc_backups"
RETAIN_DAYS = 30


def _export_one(nas):
    """Return raw CLI-export text from the router, or raise on error."""
    # RouterOS exposes `/export` as a CLI-only verb; via API we send the
    # raw CLI 'export' word, which returns the flat text back.
    with rp.RouterOSClient(nas, dry_run=False) as c:
        if c.transport_used == "api":
            # The raw 'export' command isn't in the normal path tree — use
            # the underlying librouteros send.
            out = []
            try:
                for row in c._api.rawCmd("/export"):
                    for k, v in row.items():
                        out.append(str(v))
            except Exception:
                # Fallback via /system/script
                pass
            if out:
                return "\n".join(out)
        # SSH fallback (or API fallback) — run plain CLI
        if c._ssh:
            out, _, _ = c._ssh.exec("/export")
            return out
        raise RuntimeError("No transport was able to export config")


def _rotate(nas_dir):
    cutoff = datetime.date.today() - datetime.timedelta(days=RETAIN_DAYS)
    for f in os.listdir(nas_dir):
        if not f.endswith(".rsc"):
            continue
        try:
            d = datetime.date.fromisoformat(f.replace(".rsc", ""))
        except Exception:
            continue
        if d < cutoff:
            os.remove(os.path.join(nas_dir, f))


def main():
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    db = SessionLocal()
    try:
        nas_rows = db.query(rn.NasDevice).filter(rn.NasDevice.status == "Active").all()
    finally:
        db.close()
    print(f"[{datetime.datetime.utcnow().isoformat()}Z] backing up {len(nas_rows)} NAS device(s)")
    today = datetime.date.today().isoformat()
    for nas in nas_rows:
        company = nas.company_id or "unknown"
        nas_label = "".join(c for c in (nas.name or f"nas-{nas.id}") if c.isalnum() or c in "._-")
        nas_dir = os.path.join(BACKUP_ROOT, company, nas_label)
        os.makedirs(nas_dir, exist_ok=True)
        out_path = os.path.join(nas_dir, f"{today}.rsc")
        try:
            content = _export_one(nas)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"  ✓ {company}/{nas_label} → {out_path} ({len(content)} bytes)")
            _rotate(nas_dir)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {company}/{nas_label}: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    main()

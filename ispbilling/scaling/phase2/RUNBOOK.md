# Phase 2 — Postgres Migration Runbook

## Pre-requisites
- New VM provisioned: 4 vCPU / 16 GB / 200 GB SSD (Hostinger ~₹3,500/mo)
- Postgres 16+ installed: `apt install postgresql-16 postgresql-contrib`
- Network: VPS app server can reach Postgres VM on port 5432
- Backups: pre-Phase-2 snapshot taken (DB + code)

## Day 1 — Postgres VM setup
```bash
# On the new Postgres VM:
apt update && apt install -y postgresql-16 postgresql-contrib pgloader
sudo -u postgres psql <<SQL
CREATE USER autoisp WITH PASSWORD 'CHANGE_ME_STRONG';
CREATE DATABASE autoispbilling OWNER autoisp;
ALTER ROLE autoisp SET search_path = public;
SQL

# Open Postgres to the app VPS IP only:
echo "host all all 185.199.53.93/32 md5" >> /etc/postgresql/16/main/pg_hba.conf
sed -i "s/^#listen_addresses.*/listen_addresses = '*'/" /etc/postgresql/16/main/postgresql.conf
systemctl restart postgresql

# Verify from app VPS:
psql -h <POSTGRES_VM_IP> -U autoisp -d autoispbilling
```

## Day 2 — Bulk schema + data load via pgloader
```bash
# Edit /opt/ispbilling/scaling/phase2/pgloader.conf with correct credentials
# Then on the app VPS:
scp /var/lib/autoispbilling/autoispbilling.db root@<POSTGRES_VM>:/tmp/
ssh root@<POSTGRES_VM> 'pgloader /opt/ispbilling/scaling/phase2/pgloader.conf'

# Verify row counts match
python /opt/ispbilling/scaling/phase2/reconcile.py
```

## Days 3-9 — Dual-write period
```bash
# 1. Engage dual-write in admin-portal:
echo 'DUAL_WRITE_PG=1' >> /etc/ispbilling.env
echo 'DUAL_WRITE_PG_URL=postgresql://autoisp:CHANGE_ME@<PG>:5432/autoispbilling' >> /etc/ispbilling.env

# 2. Create the outbox table + start the mirror worker
python /opt/ispbilling/scaling/phase2/mirror_worker.py &
# (productionise with a systemd unit, see below)

# 3. Daily reconciliation — schedule via timer
cat > /etc/systemd/system/isp-pg-reconcile.service <<UNIT
[Unit]
Description=AutoISP — daily Postgres reconciliation
[Service]
Type=oneshot
EnvironmentFile=/etc/ispbilling.env
ExecStart=/opt/ispbilling/venv/bin/python /opt/ispbilling/scaling/phase2/reconcile.py
User=root
UNIT

cat > /etc/systemd/system/isp-pg-reconcile.timer <<UNIT
[Unit]
Description=AutoISP — schedule pg-reconcile 04:00 IST daily
[Timer]
OnCalendar=*-*-* 22:30:00 UTC
Unit=isp-pg-reconcile.service
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload && systemctl enable --now isp-pg-reconcile.timer

# 4. Watch for drift for 7 days. Investigate ANY divergence.
#    DO NOT proceed until 7 consecutive PASS runs.
```

## Day 10 — Cutover (5-min maintenance window, 02:00 IST)
```bash
# 1. Drain in-flight requests
nginx -s reload  # with maintenance page enabled

# 2. Stop all writers
systemctl stop isp-admin isp-mobile-api isp-public isp-employee isp-superadmin isp-queue-worker

# 3. Final reconciliation - must PASS
python /opt/ispbilling/scaling/phase2/reconcile.py || exit 1

# 4. Flip DB_BACKEND
sed -i 's/DB_BACKEND=sqlite/DB_BACKEND=postgres/' /etc/ispbilling.env

# 5. Restart
systemctl start isp-admin isp-mobile-api isp-public isp-employee isp-superadmin isp-queue-worker

# 6. Remove maintenance page
nginx -s reload

# 7. Smoke test
curl https://autoispbilling.com/login   # → 200
curl https://autoispbilling.com/admin/dashboard   # → 302/login redirect
```

## Rollback (within 24h of cutover, before disabling dual-write)
```bash
# Postgres has all writes via dual-write; SQLite has them too. To roll back:
sed -i 's/DB_BACKEND=postgres/DB_BACKEND=sqlite/' /etc/ispbilling.env
systemctl restart isp-admin isp-mobile-api isp-public isp-employee isp-superadmin
# SQLite still has the most recent data — no data loss.
```

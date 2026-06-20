# Phase 3 — Read replica + horizontal app scaling

## Prerequisites
- Phase 2 complete and stable for ≥30 days
- 1 new VM for Postgres replica (2 vCPU / 8 GB / 200 GB)
- 2 new VMs for app servers (2 vCPU / 4 GB each)

## Step 1 — Postgres streaming replica
On primary Postgres VM:
```bash
sudo -u postgres psql -c "ALTER SYSTEM SET wal_level = 'replica';"
sudo -u postgres psql -c "ALTER SYSTEM SET max_wal_senders = 10;"
sudo -u postgres psql -c "CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'CHANGE_ME';"
echo "host replication replicator <REPLICA_VM_IP>/32 md5" >> /etc/postgresql/16/main/pg_hba.conf
systemctl restart postgresql
```

On new replica VM:
```bash
apt install -y postgresql-16
systemctl stop postgresql
rm -rf /var/lib/postgresql/16/main/*
sudo -u postgres pg_basebackup -h <PRIMARY_IP> -D /var/lib/postgresql/16/main \
    -U replicator -P -v -R -X stream
systemctl start postgresql
# Verify: sudo -u postgres psql -c "SELECT pg_is_in_recovery();" → t
```

## Step 2 — App code: read/write splitting
Add to /etc/ispbilling.env:
```
READ_DB_URL=postgresql://autoisp_ro:secret@<REPLICA_IP>:5432/autoispbilling
WRITE_DB_URL=postgresql://autoisp:secret@<PRIMARY_IP>:5432/autoispbilling
```
Update admin-portal/database.py to create two engines and a `get_db_read()` /
`get_db_write()` dependency pair. Wrap all SELECT queries via `get_db_read()`.

## Step 3 — Spin up app servers #2 and #3
On each new app VM, run:
```bash
# Same setup as primary VPS:
apt install -y python3-venv nginx
git clone <repo> /opt/ispbilling  # or rsync from primary
cd /opt/ispbilling && python3 -m venv venv && venv/bin/pip install -r requirements.txt
# Copy /etc/ispbilling.env from primary
# Enable + start services:
systemctl enable --now isp-admin isp-mobile-api isp-public
```

## Step 4 — Engage upstream pool in Nginx
On the load-balancer VM (the existing VPS or a new one):
```bash
# Include the upstream block:
cp /opt/ispbilling/scaling/phase3/nginx-upstream.conf /etc/nginx/conf.d/
# Replace 'proxy_pass http://127.0.0.1:8001;' → 'proxy_pass http://admin_pool;'
nginx -t && nginx -s reload
```

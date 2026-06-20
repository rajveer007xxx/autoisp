# AutoISPBilling — Full Source Snapshot

Snapshot pushed automatically on $(date) prior to the **20-phase Production Hardening**
(SQLite ➜ PostgreSQL, Nginx ➜ OpenResty, multi-server split readiness).

```
ispbilling/        Full source code (FastAPI admin-portal, workers, scripts, templates, invoices, receipts, all portals)
configs/           nginx, freeradius, isp-* systemd units (live VPS snapshot)
db/                PostgreSQL schema dump
autoispbilling_deep_audit_report.txt   Phase 1 Audit
```

Backup tarball lives on the VPS at:
`/root/autoispbilling_pre_hardening_backup_*.tar.gz`  (30-day retention)

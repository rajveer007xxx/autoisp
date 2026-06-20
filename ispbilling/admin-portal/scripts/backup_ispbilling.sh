#!/bin/bash
# backup_ispbilling.sh — Creates a clean, downloadable backup of the ISP
# Billing application and sets up a local git repository on the VPS.
#
# Output artefacts:
#   /tmp/ispbilling-backup-<DATE>.tar.gz   — full source code snapshot
#   /opt/ispbilling/admin-portal/.git       — initialised git repo (ready to push)
#
# Usage:
#   bash /tmp/backup_ispbilling.sh           # snapshot + git init + commit
#   bash /tmp/backup_ispbilling.sh --push <https_git_url> <pat_token>   # push to GH

set -euo pipefail
APP_DIR="/opt/ispbilling/admin-portal"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="/tmp/ispbilling-backup-${STAMP}.tar.gz"

cd "$APP_DIR"

echo "═════════════════════════════════════════════════════════════"
echo "  ISP Billing — backup & git snapshot (${STAMP})"
echo "═════════════════════════════════════════════════════════════"

# -------- 1. Clean, reproducible tarball ---------------------------------
# Exclude bytecode, backup turds, test caches, and the SQLite DB
# (user's live data — should be backed up separately).
TAR_EXCLUDES=(
  --exclude='__pycache__'
  --exclude='.pytest_cache'
  --exclude='*.pyc'
  --exclude='*.pyo'
  --exclude='*.bak_*'
  --exclude='*.bak'
  --exclude='backups_fork'
  --exclude='s36_rollback_archive'
  --exclude='isp_management.db'
  --exclude='*.log'
  --exclude='static/uploads'
)

echo "[1/3] Creating tarball: $OUT"
tar czf "$OUT" "${TAR_EXCLUDES[@]}" -C /opt/ispbilling admin-portal
echo "      size: $(du -h "$OUT" | cut -f1)"

# -------- 2. Initialise git repo (idempotent) ---------------------------
echo "[2/3] Git repository state"
if [ ! -d "$APP_DIR/.git" ]; then
  cd "$APP_DIR"
  git init -b main 2>&1 | tail -1
  git config user.email "admin@ispbilling.local"
  git config user.name  "ispbilling-admin"

  # .gitignore — match tar excludes
  cat > .gitignore <<'EOF'
__pycache__/
*.py[cod]
.pytest_cache/
*.bak
*.bak_*
backups_fork/
s36_rollback_archive/
static/uploads/
*.log
isp_management.db
.env
*.sqlite
*.sqlite-journal
EOF

  git add -A
  git commit -m "Initial snapshot ${STAMP} — Multi-auth + Vouchers + Captive Portal + s36b9 final backlog" 2>&1 | tail -3
else
  cd "$APP_DIR"
  git add -A
  if git diff --cached --quiet; then
    echo "      no changes to commit"
  else
    git commit -m "Snapshot ${STAMP}" 2>&1 | tail -3
  fi
fi
echo "      HEAD: $(git -C "$APP_DIR" log -1 --oneline 2>&1 || echo 'no commits yet')"
echo "      Total commits: $(git -C "$APP_DIR" rev-list --count HEAD 2>/dev/null || echo 0)"

# -------- 3. Optional push --------------------------------------------
if [ "${1:-}" = "--push" ]; then
  REMOTE_URL="${2:-}"
  TOKEN="${3:-}"
  if [ -z "$REMOTE_URL" ] || [ -z "$TOKEN" ]; then
    echo "[3/3] push skipped — usage: $0 --push <https-url> <pat-token>"
    exit 1
  fi
  # Inject token into URL safely
  AUTH_URL=$(echo "$REMOTE_URL" | sed -E "s#https://#https://x-access-token:${TOKEN}@#")
  cd "$APP_DIR"
  git remote remove origin 2>/dev/null || true
  git remote add origin "$AUTH_URL"
  echo "[3/3] pushing to $(echo "$REMOTE_URL" | sed 's#https://.*@#https://<redacted>@#') …"
  git push -u origin main 2>&1 | tail -6
  # Scrub credentials from the stored remote URL.
  git remote set-url origin "$REMOTE_URL"
  echo "      ✓ pushed and credential scrubbed from remote config"
else
  echo "[3/3] push skipped — re-run with: bash $0 --push <https-url> <pat-token>"
fi

echo
echo "Artefacts:"
echo "  • Tarball: $OUT"
echo "  • Git:     $APP_DIR/.git"
echo
echo "To download the tarball:"
echo "  scp root@185.199.53.93:${OUT} ./"

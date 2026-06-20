#!/usr/bin/env bash
# /opt/ispbilling/scripts/finish-acs-tls.sh
#
# Run this ONCE after `acs.autoispbilling.com` DNS A-record propagates to
# 185.199.53.93. It will:
#   1. Issue a Let's Encrypt cert via http-01 challenge
#   2. Atomically swap nginx vhost from HTTP-only to TLS
#   3. Reload nginx
#   4. Test that ONUs can reach https://acs.autoispbilling.com/cwmp
#
# Usage: bash /opt/ispbilling/scripts/finish-acs-tls.sh
set -e

DOMAIN="acs.autoispbilling.com"
EMAIL="admin@autoispbilling.com"

echo "==> Verifying DNS A-record"
RESOLVED=$(dig +short "$DOMAIN" @1.1.1.1 | head -1)
EXPECTED="185.199.53.93"
if [ "$RESOLVED" != "$EXPECTED" ]; then
  echo "ERROR: DNS for $DOMAIN resolves to '$RESOLVED' (expected '$EXPECTED')."
  echo "Cannot proceed — please confirm the DNS record at your provider and"
  echo "wait for global propagation (usually < 10 min)."
  exit 1
fi
echo "    DNS OK — resolves to $RESOLVED"

echo "==> Requesting Let's Encrypt cert"
certbot certonly --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --no-redirect

echo "==> Swapping nginx vhost to TLS-enabled config"
cp /etc/nginx/sites-available/acs-autoispbilling.tls \
   /etc/nginx/sites-available/acs-autoispbilling
nginx -t
systemctl reload nginx

echo "==> Smoke test"
curl -sI -o /dev/null -w 'HTTPS HTTP %{http_code}\n' "https://$DOMAIN/"
curl -sI -o /dev/null -w 'CWMP  HTTP %{http_code}\n' "https://$DOMAIN/cwmp"

echo "==> Done."
echo "You can now visit https://$DOMAIN/  (admin / AutoISP@2026)"
echo "And point your ONUs' ACS URL at https://$DOMAIN/cwmp"

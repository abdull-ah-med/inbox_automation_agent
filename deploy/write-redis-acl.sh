#!/bin/sh
# Write / refresh Redis ACL file for an existing TLS deploy (no cert regen).
# Requires REDIS_PASSWORD in the environment (same value as requirepass / REDIS_URL).
#
#   REDIS_PASSWORD=... ./deploy/write-redis-acl.sh
#
# Then restart Redis: docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d redis

set -eu
TLS_DIR="${TLS_DIR:-/opt/inbox-triage-redis-tls}"

if [ -z "${REDIS_PASSWORD:-}" ]; then
  echo "REDIS_PASSWORD is required" >&2
  exit 1
fi

sudo mkdir -p "$TLS_DIR"
# ACL: default user with password, all non-dangerous commands.
# App needs scripting (locks), streams (webhooks), hashes, and strings.
tmp="$(mktemp)"
cat > "$tmp" <<ACL
user default on >${REDIS_PASSWORD} ~* +@all -@dangerous
ACL
sudo mv "$tmp" "$TLS_DIR/users.acl"
sudo chmod 644 "$TLS_DIR/users.acl"
echo "Wrote $TLS_DIR/users.acl"

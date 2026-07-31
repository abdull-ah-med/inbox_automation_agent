#!/bin/sh
# Generates a self-signed CA + server cert for Redis's built-in TLS, and a
# random password. Redis only sits on Docker's internal network here, so a
# self-signed cert is fine — the goal is encryption-in-transit to satisfy
# this app's production checks, not public trust.
#
# Certs go in /opt/inbox-triage-redis-tls (NOT under the repo/home dir):
# the official redis image drops from root to a non-root "redis" user
# before it reads the key file, and most home dirs (e.g. Ubuntu's
# /home/ubuntu, mode 750) block that user from traversing into them at
# all — regardless of the key file's own permissions. /opt is root-owned
# and world-traversable by default, so it avoids that dead end.
#
# Run once before `docker compose ... up` (needs sudo to create /opt dir):
#   ./deploy/gen-redis-tls.sh
#
# Then put the printed REDIS_PASSWORD into both:
#   - repo-root .env         (REDIS_PASSWORD=...)
#   - backend/.env.prod      (REDIS_URL=rediss://:<password>@redis:6379/0?ssl_cert_reqs=none)

set -eu
TLS_DIR="/opt/inbox-triage-redis-tls"

sudo mkdir -p "$TLS_DIR"
sudo chown "$(id -u):$(id -g)" "$TLS_DIR"
cd "$TLS_DIR"

openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -sha256 -days 3650 -key ca.key -out ca.crt \
  -subj "/CN=inbox-triage-redis-ca"

openssl genrsa -out redis.key 2048
openssl req -new -sha256 -key redis.key -out redis.csr -subj "/CN=redis"
openssl x509 -req -sha256 -days 3650 -in redis.csr -CA ca.crt -CAkey ca.key \
  -CAcreateserial -out redis.crt
rm -f redis.csr

# The official redis image runs as uid=999(redis) gid=1000(redis), a different
# UID/GID than whoever runs this script — chown the private keys to it and
# lock them to owner-only, rather than leaving them world-readable.
sudo chown 999:1000 ca.key redis.key
sudo chmod 600 ca.key redis.key

REDIS_PASSWORD=$(openssl rand -hex 24)

echo ""
echo "Generated: $TLS_DIR/{ca.crt,ca.key,redis.crt,redis.key}"
echo ""
echo "REDIS_PASSWORD=$REDIS_PASSWORD"
echo ""
echo "Add the line above to repo-root .env, and set in backend/.env.prod:"
echo "REDIS_URL=rediss://:$REDIS_PASSWORD@redis:6379/0?ssl_cert_reqs=none"

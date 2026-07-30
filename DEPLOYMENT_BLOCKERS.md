# Deployment Blockers

Found during a deployment-readiness audit and AWS EC2 smoke test on 2026-07-30.
Status reflects the working tree at that time — **nothing below has been
committed or pushed.**

## Critical — would have broken or blocked a real AWS deploy

### 1. `docker-compose.prod.yml` silently ran the app in local mode — FIXED (uncommitted)

The base `docker-compose.yml`'s `backend.environment:` block hardcodes
`ENVIRONMENT: local`, `FRONTEND_ORIGIN: http://localhost:3000`, and
`COOKIE_SECURE: "false"` for local container networking. `docker-compose.prod.yml`
never reset that block, and Compose's `environment:` always wins over `env_file:`
— so every real value from `backend/.env.prod` (`ENVIRONMENT=production`,
the real `FRONTEND_ORIGIN`, `COOKIE_SECURE=true`) was silently discarded.

**Impact if deployed as-is:** the app would think it's running locally, skip
the entire `validate_production_security()` hardening gate, serve cookies
without `Secure`, and reject the real frontend's CORS origin (only
`localhost:3000` was allowed). It also forced `DATABASE_URL`/`REDIS_URL` to
the base file's hardcoded `postgres:postgres` credentials and plaintext
(non-TLS) Redis, regardless of what was set in `.env.prod`.

**Fix:** `docker-compose.prod.yml` now resets `environment: !reset {}` for
the backend service, making `backend/.env.prod` the sole source for
`DATABASE_URL`/`REDIS_URL`/`ENVIRONMENT`/`FRONTEND_ORIGIN`/`COOKIE_SECURE`.
Postgres's own password is now parameterized via `POSTGRES_PASSWORD` (repo-root
`.env`), matching the existing `REDIS_PASSWORD` pattern.

**How this was caught:** reviewing the YAML by eye would not have caught it —
found by actually running `docker compose -f docker-compose.yml -f
docker-compose.prod.yml up --build` end-to-end with dummy-but-valid secrets
and inspecting the resolved config/logs.

### 2. Frontend production build was broken — FIXED (uncommitted)

`Frontend/web/Dockerfile` does `COPY --from=build /app/public ./public`, but
`Frontend/web/public/` never existed in the project. Every production build
(`docker compose -f docker-compose.prod.yml build`) would fail outright.

**Fix:** added `Frontend/web/public/.gitkeep` so the directory exists and the
`COPY` succeeds. (Confirmed by re-running the build — it now completes and
the frontend serves real HTML.)

## Important — should be fixed before relying on this in production

### 3. The draft-feedback transaction bug is still uncommitted

From the earlier QA pass: `get_current_user` (`backend/app/core/dependencies_auth.py`)
queried the DB without committing, leaving an implicit transaction open that
collided with every mutating route's `async with session.begin():` —
approve/reject/wrong on drafts and all skills CRUD returned 500 against a
real Postgres session. Fixed with a 4-line commit-on-lookup change, verified
against a real Postgres instance. **This must ship for those features to work
in production at all** — currently sitting uncommitted in the working tree.

### 4. `backend/.env.example` didn't exist — FIXED (uncommitted)

The README's setup and production runbook both instruct
`cp backend/.env.example backend/.env(.prod)`, but the file was missing
entirely — a fresh clone/deploy could not follow the documented steps.
Created it with every `Settings` field and placeholder values (no real
secrets).

### 5. `TRUST_X_FORWARDED_FOR` wasn't documented as required in production

Nginx sits in front of the backend in the intended prod topology. If
`TRUST_X_FORWARDED_FOR` stays `false` (the default), rate limiting keys on
nginx's own loopback IP for every client — one shared bucket instead of
per-user limits. Now called out explicitly in the README and
`backend/.env.example`.

### 6. Redis TLS private keys were world-readable — FIXED (uncommitted)

`deploy/gen-redis-tls.sh` set `ca.key`/`redis.key` to `chmod 644`. Tightened
to `chown 999:1000` (the `redis:7-alpine` container's UID/GID) + `chmod 600`.
Verified this doesn't break Redis startup by running a real TLS-enabled
`redis:7-alpine` container against the tightened keys (`PONG` over TLS).

### 7. Missing Anthropic/Graph credentials didn't fail startup — FIXED (uncommitted)

`validate_production_security()` didn't check `ANTHROPIC_API_KEY` or the
Graph credentials — the app could pass `/health` in production while
completely unable to triage email or poll mailboxes (it would only fail,
loudly, on the first poll/triage attempt). Added to the hard startup gate so
misconfiguration is caught at deploy time instead.

### 8. README's Production (EC2) section was out of date — FIXED (uncommitted)

Predated the nginx/TLS-Redis/frontend-service additions — didn't mention
`deploy/gen-redis-tls.sh`, nginx+certbot, the `frontend` Compose service, or
the repo-root `.env` (`REDIS_PASSWORD`, `POSTGRES_PASSWORD`,
`NEXT_PUBLIC_API_BASE_URL`) it now requires. Rewrote to match current state,
plus added EC2-specific provisioning steps (Elastic IP, security groups,
Docker-on-boot).

## Open — not fixed, needs a decision

### 9. No automated database backup

Postgres data lives in a named Docker volume on the EC2 instance's root EBS
volume. There is no scheduled `pg_dump`, S3 export, or EBS snapshot policy.
Losing the instance today means losing all triage history and drafts.
Recommend a periodic `pg_dump` to S3 or enabling AWS Backup on the root
volume before relying on this in production.

## Verification performed

- Full local smoke test of `docker-compose.yml` + `docker-compose.prod.yml`
  (isolated project name/ports, no impact on the existing dev stack): built
  both images, started Postgres/Redis/backend/frontend, confirmed migrations
  ran, Redis connected over real TLS, the hardening gate passed with genuine
  `ENVIRONMENT=production`, `/health` returned `{"status":"ok"}`, and the
  frontend served real HTML.
- `nginx.conf.template` rendered with a test hostname and validated with
  `nginx -t` inside a container — syntactically clean.
- Full backend quality gate (`pytest -x`, `ruff check`, `ruff format --check`,
  `mypy app`) re-run after every fix — clean except pre-existing, unrelated
  findings from earlier commits (documented separately, out of scope here).

## Nothing here is committed

All fixes above are currently uncommitted changes in the working tree. Review
and commit when ready.

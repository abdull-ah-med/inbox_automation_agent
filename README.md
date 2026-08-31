# Inbox Triage Automation

Read-only email triage for shared Outlook mailboxes. The system ingests mail through Microsoft Graph, classifies and drafts responses with Claude, and surfaces actionable results in a secure web dashboard. A human sends the final reply from Outlook.

**This application never writes to a mailbox.** Graph access is limited to `Mail.Read`.

## What it does

1. **Ingest** — The Graph interval poller pulls Inbox, Junk, and Sent Items into PostgreSQL (optional webhooks can supplement).
2. **Triage** — Rules and LLM classification decide spam, reply-needed, or context lookup.
3. **Draft** — Claude generates suggested replies where appropriate.
4. **Review** — An invite-only web dashboard shows teaching notes, state, urgency, and drafts across four mailboxes.
5. **Audit** — Actions are logged for traceability.

The web app is read-only over pipeline output. It does not send, move, delete, or draft mail in Outlook.

## Repository layout

```
backend/          FastAPI app, Graph client, workers, auth, dashboard APIs, tests
frontend/web/     Product Next.js app (login + dashboard + mailbox/thread views)
frontend/demo/    Static UI demo (design reference only)
```

## Requirements

- Python 3.11+
- Node.js 20+ (for `frontend/web`)
- PostgreSQL with pgvector
- Redis
- Microsoft Entra app registration (`Mail.Read`, application permissions)
- Anthropic API key
- Slack app (optional / legacy review path: bot token + signing secret)

## Docker

### Local

Runs Postgres (pgvector), Redis, and the FastAPI API (`Dockerfile` target `local`, hot reload). Frontend stays on the host.

```bash
# Ensure backend/.env exists (from .env.example) with Graph, Anthropic, JWT, etc.
# Compose overrides DATABASE_URL and REDIS_URL to the container network.
docker compose up --build
```

- API: http://localhost:8000 — health: `GET /health`
- Postgres: `localhost:5432` (`postgres` / `postgres`, db `inbox_triage`)
- Redis: `localhost:6379`

Stop any host Postgres/Redis already bound to those ports first.

```bash
docker compose exec backend python -m scripts.seed_user --email you@example.com --password 'YourSecurePass1!'
```

Optional host frontend: `cd frontend/web && npm run dev`. Leave `NEXT_PUBLIC_API_BASE_URL` empty so the Next.js rewrite proxies `/auth` and `/api` to FastAPI (`BACKEND_PROXY_URL`, default `http://localhost:8000`) and CSRF cookies stay same-origin.

### Production (EC2)

Uses `Dockerfile` target `production` (no reload, non-root) plus the prod overlay, which also builds and runs the Next.js frontend. Both backend and frontend bind to `127.0.0.1` only — a host nginx (with a real TLS cert) is the sole public entry point and reverse-proxies to both. Outside `ENVIRONMENT=local`, `Settings.validate_production_security()` refuses to start the app unless TLS Redis (`rediss://` + password), a non-localhost `DATABASE_URL`, HTTPS `FRONTEND_ORIGIN`, a `JWT_SECRET` (≥64 chars), and the Anthropic/Graph/MSAL credentials are all set. Slack bot token / signing secret / channel are required **only when** `SLACK_ENABLED=true` — see `backend/.env.example` for the full list. CI deploy pins the EC2 checkout to the verified `GITHUB_SHA` (not branch tip).

0. **Provision the EC2 instance** (one-time):

   - Ubuntu 22.04/24.04 LTS, `t3.small` or larger (Postgres + Redis + backend + frontend on one box)
   - Allocate an **Elastic IP** and associate it before doing anything else — the `sslip.io` hostname below encodes this IP, so it must not change once you issue a TLS cert for it
   - Security group: inbound `22` (SSH, restrict to your admin CIDR), `80` and `443` (from anywhere, needed for HTTP→HTTPS redirect and the certbot challenge). Nothing else needs to be open — backend (`8000`) and frontend (`3000`) only bind to `127.0.0.1`, never the instance's public interface
   - Install Docker Engine + Compose plugin ([official install script](https://docs.docker.com/engine/install/ubuntu/)), then `sudo systemctl enable --now docker` so the stack (all services run `restart: unless-stopped`) comes back up automatically after an instance stop/reboot
   - Install nginx and certbot: `sudo apt install -y nginx certbot python3-certbot-nginx`
   - Clone this repo onto the instance

1. **Generate the Redis TLS cert + password** (once):

   ```bash
   ./deploy/gen-redis-tls.sh
   ```

   This prints a `REDIS_PASSWORD` — add it to a repo-root `.env` (`REDIS_PASSWORD=...`, used by `docker-compose.prod.yml` for the Redis container). Also add a `POSTGRES_PASSWORD=...` (any strong random value, e.g. `openssl rand -hex 24`) to the same repo-root `.env` — it sets the Postgres container's password.

2. **Fill in secrets**:

   ```bash
   cp backend/.env.example backend/.env.prod   # fill production values
   ```

   `backend/.env.prod` is the **only** source of `DATABASE_URL`/`REDIS_URL`/`ENVIRONMENT`/`FRONTEND_ORIGIN`/`COOKIE_SECURE` for the production container — set:

   - `DATABASE_URL=postgresql+asyncpg://postgres:<POSTGRES_PASSWORD from step 1>@postgres:5432/inbox_triage`
   - `REDIS_URL=rediss://:<REDIS_PASSWORD from step 1>@redis:6379/0`
   - `REDIS_SSL_CA_CERTS=/tls/ca.crt` — verify Redis TLS with the CA from `deploy/gen-redis-tls.sh` (do not use `ssl_cert_reqs=none`)
   - `API_HOST=<your public hostname>` — must match the Host header nginx forwards (not `localhost`)
   - `TRUST_X_FORWARDED_FOR=true` — nginx sets `X-Real-IP` / `X-Forwarded-For` to `$remote_addr`; leaving this `false` makes rate limiting key on nginx's own IP for every client instead of per-user

   Also set `NEXT_PUBLIC_API_BASE_URL` (the public `https://` API URL) in the repo-root `.env` — it's passed as a frontend build arg.

3. **Bring up the stack**:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
   ```

   - Postgres/Redis stay on the internal Compose network (not published); backend/frontend publish to `127.0.0.1` only
   - Migrations run on container start via the entrypoint (crashes the container if a migration fails, rather than starting on a stale schema)
   - Seed the first user: `docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backend python -m scripts.seed_user --email you@example.com --password 'YourSecurePass1!'`

4. **Set up nginx + TLS** on the host:

   ```bash
   sed "s/__HOST__/your.sslip.io.hostname/g" deploy/nginx.conf.template | sudo tee /etc/nginx/sites-available/inbox-triage
   sudo ln -s /etc/nginx/sites-available/inbox-triage /etc/nginx/sites-enabled/
   sudo certbot --nginx -d your.sslip.io.hostname
   ```

   `certbot` rewrites the config in place to add the HTTPS server block and HTTP→HTTPS redirect. It also installs a renewal timer — confirm with `sudo certbot renew --dry-run`.

**Backups**: Postgres data lives in a named Docker volume on the instance's root EBS volume — there is no automated off-instance backup. At minimum, schedule a periodic `pg_dump` to S3 (or enable [AWS Backup](https://docs.aws.amazon.com/aws-backup/latest/devguide/whatisbackup.html) on the root EBS volume) before relying on this in production; losing the instance currently means losing triage history and drafts.

## Backend setup

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.lock
pip install -e ".[dev]"

cp .env.example .env   # fill in Graph, Redis, DB, Anthropic, JWT, frontend origin
alembic upgrade head
uvicorn app.main:app --reload
```

Health check: `GET /health`

### Create the first web user (invite-only)

There is no public registration. Seed a user after migrations:

```bash
cd backend
source venv/bin/activate
python -m scripts.seed_user --email you@example.com --password 'YourSecurePass1!'
```

## Web dashboard setup

```bash
cd frontend/web
cp .env.example .env.local   # NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm install
npm run dev
```

Open http://localhost:3000 — sign in with the seeded user.

### What you get

- **Login** — invite-only email/password with short-lived access tokens and rotating refresh cookies
- **Home** — overview of all four mailboxes (counts, urgency mix, recent activity)
- **Mailbox** — actionable thread list (spam / no-action filtered by default)
- **Thread** — conversation plus teaching note, state, urgency, classification, draft, and audit

## Configuration

Settings load from `backend/.env`. Key variables:

| Variable | Purpose |
|----------|---------|
| `GRAPH_CLIENT_ID` / `GRAPH_CLIENT_SECRET` / `GRAPH_TENANT_ID` | Entra app credentials |
| `GRAPH_NOTIFICATION_URL` / `GRAPH_LIFECYCLE_URL` | Public webhook endpoints |
| `GRAPH_WEBHOOK_CLIENT_STATE` | Shared secret for notification validation |
| `TARGET_MAILBOXES` | Comma-separated mailbox addresses to monitor |
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `REDIS_SSL_CA_CERTS` | CA path for Redis TLS verify (required outside local) |
| `API_HOST` | Public Host header for TrustedHostMiddleware |
| `ANTHROPIC_API_KEY` | Claude API access |
| `JWT_SECRET` | Signing key for web access tokens (≥64 chars outside local) |
| `FRONTEND_ORIGIN` | Allowed web origin (e.g. `http://localhost:3000`) |
| `SLACK_ENABLED` | Opt-in Slack review cards (default `false`) |
| `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` / `SLACK_REVIEW_CHANNEL_ID` | Required only when `SLACK_ENABLED=true` |

Frontend (`frontend/web/.env.local`):

| Variable | Purpose |
|----------|---------|
| `NEXT_PUBLIC_API_BASE_URL` | Backend base URL; leave empty for same-origin (local rewrites / nginx) |
| `BACKEND_PROXY_URL` | Dev-only FastAPI origin for Next rewrites (default `http://localhost:8000`) |

## Graph integration

- **Webhooks** — `POST /webhooks/graph/notifications` and `/webhooks/graph/lifecycle`
- **Subscriptions** — Created and renewed on a schedule at startup
- **Poll fallback** — Periodic poll catches messages missed by notifications
- **Simulate ingest** — `POST /simulate/ingest` runs the intake path without calling Graph (demos/tests)

## Auth and dashboard APIs

- **Auth** — `/auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/me`, `/auth/change-password`
- **Dashboard** — `/api/dashboard` (four-mailbox overview)
- **Mailboxes** — `/api/mailboxes/{mailbox}/threads` (cursor-paginated, filterable)
- **Threads** — `/api/threads/{id}` (messages, classification, draft, state, audit)

Auth uses short-lived JWTs, HttpOnly refresh cookies, CSRF on cookie-mutating routes, CORS allowlisting, rate limits, and security headers.

## Skill import

Admins can upload Claude Agent Skill archives (`.zip` / `.skill`) from **Settings**. The archive must follow Anthropic packaging (one root folder + `SKILL.md` frontmatter). `references/` and `assets/` are stored for progressive disclosure during draft generation via the `read_skill_reference` tool. `scripts/` is skipped — this app never executes imported code (Mail.Read / read-only constraint).

On upload, the importer checks exact zip hash and skill name first. It also embeds the skill name + description and compares against existing skills; near-duplicates prompt **Overwrite** (target an existing skill) or **Create as new** (optional name override).

## Feedback loop (two-button)

On a thread draft, reviewers use **Approve** (optional edit in the preview dialog) or **Reject** (reason + note). Choosing "Wrong action / no reply needed" routes to the mark-wrong path and does not write rejection memory; other reject reasons teach the system for future drafts. Email is never sent from the app.

Approve also accepts an optional **learning context** (why a change was made) with scope **this thread only** (`once`) or **similar emails** (`similar`). Scope `similar` stores the approved body + note in reply memory for future drafts; `once` is audited only.

Reviewers can edit **urgency** inline (pencil on the urgency badge) with a required short reason. Those corrections are embedded and retrieved as urgency hints at draft time.

## Sent reply → resolved

Each mailbox's interval poller also reads **Sent Items** (still Mail.Read / read-only). When a reply is sent from Outlook, the system links it to the thread, transitions state to `RESOLVED`, and the thread page shows proposed draft vs what was actually sent. Optional Graph webhooks can supplement polling when notification URLs are configured.

## Running tests

```bash
# Backend
cd backend
source venv/bin/activate
pytest tests/ -q

# Web app
cd frontend/web
npm test
```

## Frontend demo (design reference)

```bash
cd frontend/demo
npm install
npm run dev
```

Open http://localhost:3000 — static sample UI only; not the product app.

## Branches

| Branch | Purpose |
|--------|---------|
| `main` | Production-ready baseline |
| `staging` | Pre-production integration |
| `dev` | Active development |
| `feat/haiku-triage` | AI triage, drafts, embeddings, cross-thread context |
| `feat/web-dashboard` | Invite-only auth + product web dashboard (based on `feat/haiku-triage`) |

## Security notes

- Graph credentials, JWT secrets, and API keys belong in environment variables, not in source control.
- Webhook endpoints validate `clientState` before processing notifications.
- Web access is invite-only; refresh sessions expire after seven days and are revoked on password change.
- The service does not send, move, delete, or draft mail in Outlook.

## License

Proprietary — Sample Services / Sample Information Company.

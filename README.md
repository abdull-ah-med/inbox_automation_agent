# Inbox Triage Automation

Read-only email triage for shared Outlook mailboxes. The system ingests mail through Microsoft Graph, classifies and drafts responses with Claude, and surfaces actionable results in a secure web dashboard. A human sends the final reply from Outlook.

**This application never writes to a mailbox.** Graph access is limited to `Mail.Read`.

## What it does

1. **Ingest** — Graph change notifications (with poll fallback) pull new messages into PostgreSQL.
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
| `ANTHROPIC_API_KEY` | Claude API access |
| `JWT_SECRET` | Signing key for web access tokens (≥64 chars outside local) |
| `FRONTEND_ORIGIN` | Allowed web origin (e.g. `http://localhost:3000`) |
| `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` / `SLACK_REVIEW_CHANNEL_ID` | Optional Slack review path |

Frontend (`frontend/web/.env.local`):

| Variable | Purpose |
|----------|---------|
| `NEXT_PUBLIC_API_BASE_URL` | Backend base URL (default `http://localhost:8000`) |

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

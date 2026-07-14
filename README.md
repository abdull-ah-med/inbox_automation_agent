# Inbox Triage Automation

Read-only email triage for shared Outlook mailboxes. The system ingests mail through Microsoft Graph, classifies and drafts responses with Claude, and posts review items to Slack. A human sends the final reply from Outlook.

**This application never writes to a mailbox.** Graph access is limited to `Mail.Read`.

## What it does

1. **Ingest** — Graph change notifications (with poll fallback) pull new messages into PostgreSQL.
2. **Triage** — Rules and LLM classification decide spam, reply-needed, or context lookup.
3. **Draft** — Claude generates suggested replies where appropriate.
4. **Review** — Drafts and metadata are posted to a Slack channel for human approval.
5. **Audit** — Actions are logged for traceability.

## Repository layout

```
backend/          FastAPI app, Graph client, workers, services, tests
frontend/demo/    Next.js UI demo for the triage workflow
```

## Requirements

- Python 3.11+
- PostgreSQL with pgvector
- Redis
- Microsoft Entra app registration (`Mail.Read`, application permissions)
- Anthropic API key
- Slack app (bot token + signing secret)

## Backend setup

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.lock

cp .env.example .env   # fill in Graph, Redis, DB, Slack, Anthropic values
alembic upgrade head
uvicorn app.main:app --reload
```

Health check: `GET /health`

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
| `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` / `SLACK_REVIEW_CHANNEL_ID` | Slack integration |
| `ANTHROPIC_API_KEY` | Claude API access |

## Graph integration

- **Webhooks** — `POST /webhooks/graph/notifications` and `/webhooks/graph/lifecycle`
- **Subscriptions** — Created and renewed on a schedule at startup
- **Poll fallback** — Periodic poll catches messages missed by notifications
- **Simulate ingest** — `POST /simulate/ingest` runs the intake path without calling Graph (demos/tests)

## Running tests

```bash
cd backend
source venv/bin/activate
pytest tests/ -q
```

## Frontend demo

```bash
cd frontend/demo
npm install
npm run dev
```

Open http://localhost:3000

## Branches

| Branch | Purpose |
|--------|---------|
| `main` | Production-ready baseline |
| `staging` | Pre-production integration |
| `dev` | Active development |
| `feat/ms-graph-mail-ingestion` | Graph ingestion, subscriptions, poll fallback |

## Security notes

- Graph credentials and API keys belong in environment variables, not in source control.
- Webhook endpoints validate `clientState` before processing notifications.
- The service does not send, move, delete, or draft mail in Outlook.

## License

Proprietary — Sample Services / Sample Information Company.

# Distributor CRM — Customer Management AI Agent

A self-contained, world-class **customer-management AI agent** for independent
distributors. It helps you manage customers, track their progress and orders,
follow their social-media activity and comments, surface personalized tips,
**catch up on everyone on a regular basis**, and — when it detects a customer
needs support — **reminds you to call or text** them with a ready-to-send message.

It runs with **zero external dependencies beyond Python** (SQLite + FastAPI) and
works fully offline using a deterministic, explainable rule engine. Add an
`OPENAI_API_KEY` and the same engine's signals are turned into richer,
natural-language tips, messages, and daily briefings.

---

## What it does

| Capability | How |
|---|---|
| **Manage customers** | Full CRUD: profile, stage (prospect → active → vip), tags, socials, preferred channel, notes. |
| **Track progress** | Milestones per customer (`planned / in_progress / done / stalled`). |
| **Track orders** | Orders with product, amount, status, date → revenue + reorder timing. |
| **Social activity & comments** | Log posts, comments, DMs, mentions, reviews across Instagram / Facebook / TikTok / X / LinkedIn / WhatsApp, with automatic sentiment detection and "needs reply" flags. |
| **Regular tips** | AI (or rule-based) personalized nurture tips per customer. |
| **Frequent catch-ups** | One-click **daily catch-up** ranks who needs you today and logs your outreach. |
| **Support detection → reminders** | Health scoring detects at-risk customers, negative sentiment, unanswered comments and stale relationships, then files **call/text reminders** with a drafted message. |
| **Ask AI** | Chat over your whole portfolio: "Who should I call today?", "Draft a reorder text for James." |
| **🤖 Morning briefing + voice** | Open the app and Jarvis reads you a spoken + text rundown: new customers, who ordered, who's *in town* (from social), how many posts it reviewed/replied, and your personalized next steps. Talk back with the mic. |
| **Communication plans** | Multi-step nurture cadences (e.g. welcome → day‑3 tip → day‑10 call → day‑21 reorder). The scheduler advances due steps automatically. |
| **Autonomous social monitor** | Reviews recent posts/comments, drafts replies, and applies the autonomy policy. |
| **Content inspiration** | Turns the latest Herbalife CEO / brand news into ready-to-publish repost drafts in the distributor's own voice. |

### Autonomy policy — *auto-handle low-risk only*

- **Low-risk** (reply to a positive/neutral post, a routine plan touch) → the agent
  **does it automatically** and logs it.
- **Sensitive** (negative sentiment, a phone call, anything flagged) → the agent
  **drafts it and queues it for your approval** (Approvals tab), plus files a reminder.

Every autonomous action is written to `agent_actions` for full transparency — that's
how the briefing can tell you "I reviewed 6 posts and replied to 5."

### How this maps to the autonomous "Jarvis on remote infra" vision

| Vision | In this codebase |
|---|---|
| Always-on agent on remote infra | `POST /api/agent/tick` + `/api/agent/monitor` — call from cron/a worker |
| Talk to it on your phone | Voice briefing + mic (Web Speech API) in the Jarvis tab; PWA-ready |
| Pull customers from Herbalife | `connectors.HerbalifeConnector` (browser automation, credential-gated) |
| Map customers → social, monitor & respond | `socials` field + `agent_ops.monitor()` + Meta connector seam |
| Communication plan | `comm_plans` / `enrollments` + scheduler `tick()` |
| Repost from CEO news | `connectors.fetch_ceo_posts()` + `ai_agent.content_ideas()` |

> **Security note (important):** credentials for Herbalife / social platforms are
> **never stored in the app database or source**. Connectors read them at runtime
> from environment variables (in production, a secrets manager) — see
> `app/connectors.py`. Automating a third-party portal may conflict with its
> Terms of Service and should only be run against the distributor's own account,
> with consent. Outbound replies default to **approve-first for anything sensitive**.

### The "brain": health scoring (deterministic & explainable)

Each customer gets a **0–100 health score** from signals such as days since last
contact, days since last order (for active/VIP), recent negative social
sentiment, unanswered comments, open follow-ups, and stalled goals. The score
maps to `healthy / nurture / at_risk`, and the engine decides whether the
customer **needs support**, the **urgency**, and the **best channel** (urgent →
call, otherwise the customer's preferred channel). See `app/scoring.py` — it's
pure, testable, and works without any API key.

---

## Run it locally

```bash
cd customer_agent
pip install -r requirements.txt

# (optional) load demo customers
python -m app.seed --reset

# start the server (serves both API and web UI)
uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000** for the web app, or **/docs** for the API.

### Enable AI (optional)

```bash
export OPENAI_API_KEY=sk-...        # add to customer_agent/.env or your shell
export OPENAI_CHAT_MODEL=gpt-4o-mini  # default
```

Without a key, every AI feature transparently falls back to the built-in
rule-based generator — the product is fully functional either way.

---

## Docker

```bash
cd customer_agent
docker build -t distributor-crm .
docker run -p 8000:8000 \
  -e OPENAI_API_KEY=sk-... \
  -v "$(pwd)/data:/app/data" \
  distributor-crm
```

---

## API overview

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Service status + whether AI is enabled |
| GET/POST | `/api/customers` | List (with health) / create |
| GET/PATCH/DELETE | `/api/customers/{id}` | Full detail / update / delete |
| POST | `/api/customers/{id}/orders` | Add order |
| POST | `/api/customers/{id}/activities` | Log social activity/comment (auto-sentiment) |
| POST | `/api/customers/{id}/progress` | Add milestone |
| POST | `/api/customers/{id}/interactions` | Log a catch-up (call/text/etc.) |
| GET | `/api/customers/{id}/tips` | Personalized tips |
| POST | `/api/customers/{id}/draft` | Draft a call script / text |
| GET | `/api/agent/digest` | **Daily catch-up**: ranked focus list, briefing, auto-reminders |
| GET | `/api/agent/briefing` | **Morning briefing**: rundown + voice narration (runs monitor+tick) |
| POST | `/api/agent/monitor` | Review social activity, auto-reply low-risk, queue sensitive |
| POST | `/api/agent/tick` | Advance due communication-plan steps (cron/worker) |
| GET/PATCH | `/api/agent/actions` | List / approve / reject queued agent actions |
| GET/POST | `/api/plans` | List / create communication plans |
| POST | `/api/enrollments` | Enroll a customer in a plan |
| GET | `/api/content/ceo-feed` | Latest CEO posts (inspiration source) |
| POST | `/api/content/ideas` | Generate repost drafts from a topic/source |
| GET | `/api/connectors` | Connector configuration status |
| POST | `/api/connectors/herbalife/sync` | Import customers/orders (browser automation) |
| POST | `/api/connectors/social/sync` | Pull social activity (Meta Graph API) |
| POST | `/api/agent/chat` | Conversational agent over your portfolio |
| GET | `/api/reminders` | Reminders (filter by status) |
| POST | `/api/customers/{id}/reminders` | Create reminder |
| PATCH | `/api/reminders/{id}` | Update / done / dismiss |

---

## Project structure

```
customer_agent/
├── app/
│   ├── main.py        # FastAPI app + all routes
│   ├── database.py    # SQLite schema & helpers (stdlib only)
│   ├── models.py      # Pydantic schemas
│   ├── scoring.py     # Deterministic health scoring & support detection
│   ├── ai_agent.py    # LLM layer with rule-based fallback
│   └── seed.py        # Demo data
├── web/               # Vanilla SPA (no build step)
│   ├── index.html  styles.css  app.js  config.js
├── tests/             # Scoring + API tests
├── requirements.txt   Dockerfile  .env.example  README.md
```

## Tests

```bash
cd customer_agent
python -m tests.test_agent
```

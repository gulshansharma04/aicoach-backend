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

### Onboarding (Jarvis-style first run)

Open the app and the Distributor Buddy walks you through: a spoken welcome →
sign up with **email or phone + 2-factor OTP** → a capabilities pitch →
**trust & consent toggles** → choose which **social platforms** to track →
**connect myHerbalife**. Your consent choices actually govern the agent's
behavior (e.g. *draft-only* means even low-risk replies are queued for approval
instead of auto-sent). Credentials are held in a **secret store, never the DB**
(`app/secret_store.py`).

### Extra agent capabilities

- **Mobile notifications** — the agent files notifications (and pushes via FCM/APNs
  when configured); urgent items also fire a native browser/PWA notification.
- **Email monitoring** — scans subscribed myHerbalife emails, triages importance,
  and notifies you about what matters (`/api/agent/scan-email`).
- **Website builder** — generates a buildable product-microsite brief and hands it
  to **Google Stitch** (`/api/website/plan`).

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

## Test it on your iPhone

The app is a mobile-friendly PWA, so you don't need the App Store to try it.

**Option A — quickest (same Wi-Fi):**
1. Run the server on your computer, bound to all interfaces:
   `uvicorn app.main:app --host 0.0.0.0 --port 8000`
2. Find your computer's LAN IP (e.g. `192.168.1.42`).
3. On your iPhone (same Wi-Fi), open Safari → `http://192.168.1.42:8000`.
4. Go through the Jarvis onboarding. In **Share → Add to Home Screen** to install
   it like an app (full-screen, its own icon).

**Option B — from anywhere (deploy):** push this folder to any host that runs a
container (Render, Railway, Fly.io, etc.) using the included `Dockerfile`, set
`OPENAI_API_KEY` (optional) as an env var, then open the HTTPS URL in Safari and
Add to Home Screen.

**Notes for iOS:**
- **Voice:** "Play briefing" and "Talk to Jarvis" use the browser's Speech APIs.
  Speech *synthesis* (Jarvis talking) works in Safari. Speech *recognition* (the
  mic) is best on Chrome/desktop; on iOS use the on-screen keyboard's dictation
  in the "Ask AI" box as a fallback.
- **Notifications:** native web-push on iOS requires iOS 16.4+ **and** the app
  added to the Home Screen. Until a push provider (FCM/APNs) is wired, in-app
  notifications (the 🔔) always work.
- For a true native iOS app later, this same `web/` folder can be wrapped with
  Capacitor (the repo already uses it) and shipped via TestFlight.

## Project structure

```
customer_agent/
├── app/
│   ├── main.py        # FastAPI app + all routes
│   ├── database.py    # SQLite schema & helpers (stdlib only)
│   ├── models.py      # Pydantic schemas
│   ├── scoring.py     # Deterministic health scoring & support detection
│   ├── ai_agent.py    # LLM layer with rule-based fallback
│   ├── agent_ops.py   # Plans, scheduler tick, social monitor, briefing, consent
│   ├── auth.py        # Signup + 2FA OTP + sessions
│   ├── secret_store.py# Credential vault (never the DB)
│   ├── connectors.py  # Herbalife / social / email / Stitch seams
│   ├── notify.py      # Notifications + mobile push seam
│   └── seed.py        # Demo data
├── web/               # Vanilla SPA + PWA (no build step)
│   ├── index.html  onboarding.html  styles.css  app.js  onboarding.js
│   ├── config.js  manifest.json
├── tests/             # 57-check suite
├── requirements.txt   Dockerfile  .env.example  README.md
```

## Tests

```bash
cd customer_agent
python -m tests.test_agent
```

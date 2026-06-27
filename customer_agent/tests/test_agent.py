"""
Dependency-light tests (no pytest required).

Run from the customer_agent/ directory:
    python -m tests.test_agent
"""

from __future__ import annotations

import os
import tempfile

# Use a throwaway DB before importing the app modules.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["CRM_DB_PATH"] = _tmp.name

from app import database as db          # noqa: E402
from app import scoring, ai_agent       # noqa: E402
from app.seed import seed, reset        # noqa: E402

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


def test_sentiment():
    check("positive sentiment", ai_agent.detect_sentiment("I love this, it's amazing!") == "positive")
    check("negative sentiment", ai_agent.detect_sentiment("This is broken and I'm disappointed") == "negative")
    check("neutral sentiment", ai_agent.detect_sentiment("Can you tell me the price?") == "neutral")


def test_scoring_healthy():
    cust = {"stage": "active", "preferred_channel": "text"}
    recent = scoring.iso_now()
    h = scoring.compute_health(
        cust,
        orders=[{"amount": 100, "ordered_at": recent}],
        activities=[{"sentiment": "positive", "occurred_at": recent, "needs_response": 0}],
        interactions=[{"occurred_at": recent}],
        progress=[], reminders=[],
    )
    check("healthy score >= 75", h["score"] >= 75)
    check("healthy status", h["status"] == "healthy")
    check("healthy needs no support", h["needs_support"] is False)


def test_scoring_negative_triggers_call():
    cust = {"stage": "active", "preferred_channel": "text"}
    recent = scoring.iso_now()
    h = scoring.compute_health(
        cust,
        orders=[{"amount": 100, "ordered_at": recent}],
        activities=[{"sentiment": "negative", "occurred_at": recent, "needs_response": 1}],
        interactions=[{"occurred_at": recent}],
        progress=[], reminders=[],
    )
    check("negative needs support", h["needs_support"] is True)
    check("negative urgency urgent", h["suggested_urgency"] == "urgent")
    check("negative escalates to call", h["suggested_channel"] == "call")


def test_scoring_stale_contact():
    cust = {"stage": "prospect", "preferred_channel": "text"}
    old = (scoring.now_utc().replace(year=scoring.now_utc().year - 1)).isoformat()
    h = scoring.compute_health(
        cust, orders=[], activities=[], interactions=[{"occurred_at": old}],
        progress=[], reminders=[],
    )
    check("stale contact needs support", h["needs_support"] is True)
    check("stale contact lowers score", h["score"] < 75)


def test_fallback_outputs():
    cust = {"name": "Sam Doe", "stage": "active", "preferred_channel": "text"}
    h = scoring.compute_health(cust, [], [], [], [], [])
    tips = ai_agent.generate_tips(cust, h, [])
    check("tips returned", isinstance(tips, list) and len(tips) >= 1)
    msg = ai_agent.draft_message(cust, h, "text", "check in")
    check("draft message non-empty", isinstance(msg, str) and len(msg) > 0)
    check("draft uses first name", "Sam" in msg or "there" in msg)


def test_seed_and_db():
    db.init_db()
    reset()
    seed()
    with db.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM customers").fetchone()["c"]
    check("seed inserts customers", n >= 6)


def test_api_roundtrip():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    check("health endpoint", c.get("/api/health").json()["ok"] is True)

    created = c.post("/api/customers", json={"name": "Test User", "stage": "active"}).json()
    cid = created["id"]
    check("create customer", cid > 0)

    c.post(f"/api/customers/{cid}/activities",
           json={"platform": "instagram", "kind": "comment", "content": "this is broken and late"})
    detail = c.get(f"/api/customers/{cid}").json()
    check("activity auto-sentiment negative", detail["activities"][0]["sentiment"] == "negative")
    check("negative drives needs_support", detail["health"]["needs_support"] is True)

    digest = c.get("/api/agent/digest").json()
    check("digest has briefing", bool(digest["briefing"]))
    check("digest created reminders", digest["totals"]["reminders_created"] >= 1)

    rem = c.get("/api/reminders?status=open").json()
    check("reminders listed", rem["count"] >= 1)

    c.delete(f"/api/customers/{cid}")
    check("delete customer", c.get(f"/api/customers/{cid}").status_code == 404)


if __name__ == "__main__":
    print("Running customer-agent tests...\n")
    for fn in [test_sentiment, test_scoring_healthy, test_scoring_negative_triggers_call,
               test_scoring_stale_contact, test_fallback_outputs, test_seed_and_db,
               test_api_roundtrip]:
        print(fn.__name__)
        fn()
    print(f"\n{_passed} passed, {_failed} failed")
    raise SystemExit(1 if _failed else 0)

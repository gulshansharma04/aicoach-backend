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
from app import scoring, ai_agent, agent_ops, connectors  # noqa: E402
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


def test_travel_detection():
    check("travel detected", ai_agent.detect_travel("Back in town visiting family!") is True)
    check("no travel", ai_agent.detect_travel("Loving my morning shake") is False)


def test_risk_policy():
    check("negative is sensitive", agent_ops.classify_risk("social", sentiment="negative") == "sensitive")
    check("positive is low", agent_ops.classify_risk("social", sentiment="positive") == "low")
    check("call is sensitive", agent_ops.classify_risk("call") == "sensitive")


def test_content_ideas():
    ideas = ai_agent.content_ideas("CEO keynote", "great speech", ["instagram"], 2)
    check("content ideas count", len(ideas) >= 1)
    check("content has caption", bool(ideas[0]["caption"]))


def test_connectors_status():
    s = connectors.status()
    check("herbalife unconfigured by default", s["herbalife"]["configured"] is False)
    check("herbalife uses browser automation", s["herbalife"]["method"] == "browser_automation")


def test_plans_monitor_briefing():
    db.init_db(); reset(); seed()
    with db.get_conn() as conn:
        mon = agent_ops.monitor(conn)
        check("monitor reviewed posts", mon["reviewed"] >= 1)
        check("monitor auto-replied to low-risk", mon["auto_replied"] >= 1)
        check("monitor queued a sensitive reply", mon["queued"] >= 1)
        tick = agent_ops.tick(conn)
        check("tick processed due plan steps", tick["processed"] >= 1)
        b = agent_ops.build_briefing(conn, days=7, run_agent=False)
        check("briefing reports reviewed posts", b["stats"]["posts_reviewed"] >= 1)
        check("briefing reports replied posts", b["stats"]["posts_replied"] >= 1)
        check("briefing finds in-town customer", b["stats"]["in_town"] >= 1)
        check("briefing has next steps", len(b["next_steps"]) >= 1)
        check("briefing has narration", bool(b["narration"]))


def test_auth_and_consent_flow():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    su = c.post("/api/auth/signup", json={"name": "Alex", "email": "alex@example.com"}).json()
    check("signup returns dev otp", "dev_otp" in su)
    bad = c.post("/api/auth/verify", json={"distributor_id": su["distributor_id"], "code": "000000"})
    check("wrong otp rejected", bad.status_code == 400)
    ok = c.post("/api/auth/verify", json={"distributor_id": su["distributor_id"], "code": su["dev_otp"]}).json()
    check("verify returns token", bool(ok.get("token")))
    token = ok["token"]
    hdr = {"X-Distributor-Token": token}
    check("me unauthorized without token", c.get("/api/me").status_code == 401)
    me = c.get("/api/me", headers=hdr).json()
    check("me authorized with token", me["id"] == su["distributor_id"])
    upd = c.patch("/api/me/consent", headers=hdr,
                  json={"consent": {"social_posting": "draft"}, "onboarded": True}).json()
    check("consent saved", upd["consent"]["social_posting"] == "draft")
    check("onboarded flag set", upd["onboarded"] is True)


def test_consent_gates_monitor():
    import json as _json
    db.init_db(); reset(); seed()
    with db.get_conn() as conn:
        # Onboard a distributor with draft-only posting.
        conn.execute(
            "INSERT INTO distributors (name,consent,onboarded,created_at,updated_at) VALUES (?,?,1,?,?)",
            ("Draft User", _json.dumps({"social_posting": "draft"}), scoring.iso_now(), scoring.iso_now()))
    with db.get_conn() as conn:
        mon = agent_ops.monitor(conn)
        check("draft mode auto-replies nothing", mon["auto_replied"] == 0)
        check("draft mode queues replies", mon["queued"] >= 1)


def test_secret_store():
    from app import secret_store
    check("no creds initially", secret_store.has_credentials(999, "herbalife") is False)
    secret_store.store_credentials(999, "herbalife", {"username": "u", "password": "p"})
    check("creds stored", secret_store.has_credentials(999, "herbalife") is True)
    secret_store.revoke_credentials(999, "herbalife")
    check("creds revoked", secret_store.has_credentials(999, "herbalife") is False)


def test_email_triage_and_website():
    t = ai_agent.triage_email("Action required: your order shipped", "Tracking inside")
    check("important email flagged", t["important"] is True)
    t2 = ai_agent.triage_email("hello", "just saying hi")
    check("routine email not flagged", t2["important"] is False)
    plan = ai_agent.website_plan("Pro2col", "introduce the product")
    check("website plan has sections", len(plan.get("sections", [])) >= 1)


def test_notifications():
    from fastapi.testclient import TestClient
    from app.main import app
    db.init_db(); reset(); seed()
    with db.get_conn() as conn:
        from app import notify
        notify.notify(conn, "Test", "body", level="urgent")
    c = TestClient(app)
    n = c.get("/api/notifications").json()
    check("notification listed", n["unread"] >= 1)


if __name__ == "__main__":
    print("Running customer-agent tests...\n")
    for fn in [test_sentiment, test_scoring_healthy, test_scoring_negative_triggers_call,
               test_scoring_stale_contact, test_fallback_outputs, test_seed_and_db,
               test_api_roundtrip, test_travel_detection, test_risk_policy,
               test_content_ideas, test_connectors_status, test_plans_monitor_briefing,
               test_auth_and_consent_flow, test_consent_gates_monitor, test_secret_store,
               test_email_triage_and_website, test_notifications]:
        print(fn.__name__)
        fn()
    print(f"\n{_passed} passed, {_failed} failed")
    raise SystemExit(1 if _failed else 0)

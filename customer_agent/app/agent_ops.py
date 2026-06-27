"""
Autonomous operations: communication plans, the social monitor, the scheduler
tick, and the morning briefing.

Autonomy policy (per product decision): **auto-handle low-risk only.**
  • low-risk action (e.g. reply to a positive/neutral post, a routine plan
    touch on the customer's preferred low-touch channel) -> executed
    automatically and logged as ``auto_done``/``sent``.
  • sensitive action (negative sentiment, a call, anything flagged) -> queued
    as ``pending`` for the distributor to approve, plus a reminder.

Everything the agent does is written to ``agent_actions`` for full transparency
and so the briefing can report "I reviewed X posts and replied to Y."
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

from . import database as db
from . import ai_agent, scoring, notify
from .scoring import iso_now, now_utc, days_since


def _within(ts: Optional[str], window_days: int) -> bool:
    """True if ``ts`` is within the last ``window_days`` (handles 'today' = 0 correctly)."""
    d = days_since(ts)
    return d is not None and d <= window_days


# Default consent when no distributor has onboarded yet (legacy/back-compat:
# behave autonomously). The onboarding wizard writes the user's real choices,
# which default to the safer 'draft' (approve-first) for posting.
CONSENT_DEFAULTS: Dict[str, Any] = {
    "manage_customers": True,
    "auto_followups": True,        # auto-send low-risk plan touches
    "find_social": True,
    "social_posting": "auto",      # auto | draft | off  (reply to social posts)
    "ceo_content": "auto",         # auto | draft | off  (repost CEO inspiration)
}


def get_active_consent(conn) -> Dict[str, Any]:
    """Return the onboarded distributor's consent, merged over defaults."""
    row = conn.execute(
        "SELECT consent FROM distributors WHERE onboarded=1 ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    consent = dict(CONSENT_DEFAULTS)
    if row:
        stored = db.row_to_dict(row).get("consent") or {}
        if isinstance(stored, dict):
            consent.update({k: v for k, v in stored.items() if v is not None})
    return consent


# ============================================================
# Autonomy policy
# ============================================================

def classify_risk(channel: str, sentiment: Optional[str] = None,
                  explicit: Optional[str] = None) -> str:
    """Decide whether an action is 'low' or 'sensitive'."""
    if explicit in ("low", "sensitive"):
        return explicit
    if sentiment == "negative":
        return "sensitive"
    if channel == "call":
        return "sensitive"  # calls always involve a human
    return "low"


def _log_action(conn, *, customer_id: int, kind: str, status: str, risk: str,
                channel: str = "", platform: str = "", summary: str = "",
                draft: str = "", activity_id: Optional[int] = None,
                enrollment_id: Optional[int] = None) -> int:
    cur = conn.execute(
        """INSERT INTO agent_actions
           (customer_id,enrollment_id,activity_id,kind,platform,channel,summary,draft,risk,status,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (customer_id, enrollment_id, activity_id, kind, platform, channel,
         summary, draft, risk, status, iso_now()),
    )
    return cur.lastrowid


def _ensure_reminder(conn, customer_id: int, reason: str, draft: str,
                     channel: str, priority: str) -> bool:
    """File an agent reminder if one isn't already open for this customer."""
    existing = conn.execute(
        "SELECT id FROM reminders WHERE customer_id = ? AND source = 'agent' AND status = 'open'",
        (customer_id,),
    ).fetchone()
    if existing:
        return False
    conn.execute(
        """INSERT INTO reminders
           (customer_id,kind,reason,draft_message,priority,status,due_date,source,created_at)
           VALUES (?,?,?,?,?, 'open', ?, 'agent', ?)""",
        (customer_id, channel if channel in ("call", "text") else "follow_up",
         reason, draft, priority, iso_now(), iso_now()),
    )
    return True


# ============================================================
# Communication plans
# ============================================================

def _customer(conn, customer_id: int) -> Optional[Dict[str, Any]]:
    return db.row_to_dict(conn.execute(
        "SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone())


def list_plans(conn) -> List[Dict[str, Any]]:
    plans = db.rows_to_list(conn.execute("SELECT * FROM comm_plans ORDER BY id"))
    for p in plans:
        p["steps"] = db.rows_to_list(conn.execute(
            "SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY step_order", (p["id"],)))
        p["enrolled"] = conn.execute(
            "SELECT COUNT(*) AS c FROM enrollments WHERE plan_id = ? AND status = 'active'",
            (p["id"],)).fetchone()["c"]
    return plans


def create_plan(conn, name: str, description: str, steps: List[Dict[str, Any]]) -> int:
    cur = conn.execute(
        "INSERT INTO comm_plans (name,description,created_at) VALUES (?,?,?)",
        (name, description, iso_now()))
    pid = cur.lastrowid
    for i, s in enumerate(steps):
        conn.execute(
            """INSERT INTO plan_steps (plan_id,step_order,day_offset,channel,goal,risk)
               VALUES (?,?,?,?,?,?)""",
            (pid, i, int(s.get("day_offset", 0)), s.get("channel", "text"),
             s.get("goal", ""), s.get("risk", "low")))
    return pid


def _plan_steps(conn, plan_id: int) -> List[Dict[str, Any]]:
    return db.rows_to_list(conn.execute(
        "SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY step_order", (plan_id,)))


def enroll(conn, customer_id: int, plan_id: int) -> Dict[str, Any]:
    if not _customer(conn, customer_id):
        raise ValueError("Customer not found")
    steps = _plan_steps(conn, plan_id)
    if not steps:
        raise ValueError("Plan has no steps")
    now = iso_now()
    first_due = (now_utc() + timedelta(days=int(steps[0]["day_offset"]))).isoformat()
    cur = conn.execute(
        """INSERT INTO enrollments (customer_id,plan_id,status,current_step,next_due,started_at,updated_at)
           VALUES (?,?, 'active', 0, ?, ?, ?)""",
        (customer_id, plan_id, first_due, now, now))
    return db.row_to_dict(conn.execute(
        "SELECT * FROM enrollments WHERE id = ?", (cur.lastrowid,)).fetchone())


def _advance_enrollment(conn, enr: Dict[str, Any], steps: List[Dict[str, Any]]) -> None:
    nxt = enr["current_step"] + 1
    if nxt >= len(steps):
        conn.execute("UPDATE enrollments SET status='completed', current_step=?, next_due=NULL, updated_at=? WHERE id=?",
                     (nxt, iso_now(), enr["id"]))
    else:
        due = (now_utc() + timedelta(days=int(steps[nxt]["day_offset"]))).isoformat()
        conn.execute("UPDATE enrollments SET current_step=?, next_due=?, updated_at=? WHERE id=?",
                     (nxt, due, iso_now(), enr["id"]))


# ============================================================
# Scheduler tick — advance due plan steps
# ============================================================

def tick(conn) -> Dict[str, Any]:
    """Process every due plan step. Safe to call repeatedly (idempotent per due-time)."""
    consent = get_active_consent(conn)
    allow_auto = bool(consent.get("auto_followups", True))

    now = iso_now()
    due = db.rows_to_list(conn.execute(
        "SELECT * FROM enrollments WHERE status='active' AND next_due IS NOT NULL AND next_due <= ?",
        (now,)))
    out = {"processed": 0, "auto_sent": 0, "queued": 0}

    for enr in due:
        cust = _customer(conn, enr["customer_id"])
        if not cust:
            continue
        steps = _plan_steps(conn, enr["plan_id"])
        if enr["current_step"] >= len(steps):
            conn.execute("UPDATE enrollments SET status='completed', next_due=NULL WHERE id=?", (enr["id"],))
            continue
        step = steps[enr["current_step"]]

        # Build a light health view to personalize the touch.
        health = {"score": 70, "status": "nurture", "signals": {}, "support_reason": step["goal"]}
        draft = ai_agent.draft_message(cust, health, step["channel"], step["goal"] or "stay in touch")
        risk = classify_risk(step["channel"], explicit=step.get("risk"))

        if risk == "low" and allow_auto:
            status = "sent"
            out["auto_sent"] += 1
            # auto-sent touches are logged as a completed interaction
            conn.execute(
                "INSERT INTO interactions (customer_id,channel,summary,occurred_at) VALUES (?,?,?,?)",
                (cust["id"], step["channel"], f"[plan] {step['goal']}", iso_now()))
        else:
            status = "pending"
            out["queued"] += 1
            _ensure_reminder(conn, cust["id"], f"Plan step: {step['goal']}", draft,
                             step["channel"], "high")

        _log_action(conn, customer_id=cust["id"], enrollment_id=enr["id"], kind="plan_step",
                    channel=step["channel"], summary=step["goal"], draft=draft,
                    risk=risk, status=status)
        _advance_enrollment(conn, enr, steps)
        out["processed"] += 1

    return out


# ============================================================
# Social monitor — review posts, draft/auto-send replies
# ============================================================

def monitor(conn, days: int = 14) -> Dict[str, Any]:
    """
    Review recent social activity the agent hasn't seen yet, draft replies, and
    apply the autonomy policy. Idempotent: an activity is only processed once
    (tracked via agent_actions.activity_id).
    """
    consent = get_active_consent(conn)
    posting = consent.get("social_posting", "auto")  # auto | draft | off

    seen = {r["activity_id"] for r in db.rows_to_list(conn.execute(
        "SELECT DISTINCT activity_id FROM agent_actions WHERE activity_id IS NOT NULL"))}

    acts = db.rows_to_list(conn.execute(
        "SELECT * FROM activities ORDER BY occurred_at DESC"))
    out = {"reviewed": 0, "auto_replied": 0, "queued": 0, "skipped": 0}

    for a in acts:
        if a["id"] in seen:
            continue
        if not _within(a.get("occurred_at"), days):
            continue
        # Only customer-authored posts/comments warrant a reply.
        if a["kind"] in ("like",):
            continue

        cust = _customer(conn, a["customer_id"])
        if not cust:
            continue

        out["reviewed"] += 1
        _log_action(conn, customer_id=cust["id"], activity_id=a["id"], kind="review",
                    platform=a["platform"], summary=f"Reviewed {a['kind']}: {a['content'][:80]}",
                    risk="low", status="auto_done")

        # Respect the distributor's posting consent.
        if posting == "off":
            out["skipped"] += 1
            continue

        reply = ai_agent.draft_reply(cust, a)
        risk = classify_risk("social", sentiment=a.get("sentiment"))

        if risk == "low" and posting == "auto":
            status = "sent"
            out["auto_replied"] += 1
            # mark the original comment as handled
            conn.execute("UPDATE activities SET needs_response=0 WHERE id=?", (a["id"],))
        elif risk == "low":  # posting == 'draft' -> queue for approval
            status = "pending"
            out["queued"] += 1
        else:  # negative / sensitive -> always require approval + reminder
            status = "pending"
            out["queued"] += 1
            _ensure_reminder(conn, cust["id"],
                             f"Negative {a['platform']} post needs a personal reply",
                             reply, "call", "urgent")
            notify.notify(
                conn, f"⚠ {cust['name']} needs a personal touch",
                f"Negative {a['platform']} post — I drafted a reply for your approval.",
                level="urgent", source="monitor", link=f"#approvals")

        _log_action(conn, customer_id=cust["id"], activity_id=a["id"], kind="reply",
                    platform=a["platform"], channel="social", summary=a["content"][:80],
                    draft=reply, risk=risk, status=status)

    return out


# ============================================================
# Morning briefing
# ============================================================

def _children(conn, customer_id: int) -> Dict[str, List[Dict[str, Any]]]:
    return {
        "orders": db.rows_to_list(conn.execute(
            "SELECT * FROM orders WHERE customer_id = ?", (customer_id,))),
        "activities": db.rows_to_list(conn.execute(
            "SELECT * FROM activities WHERE customer_id = ?", (customer_id,))),
        "interactions": db.rows_to_list(conn.execute(
            "SELECT * FROM interactions WHERE customer_id = ?", (customer_id,))),
        "progress": db.rows_to_list(conn.execute(
            "SELECT * FROM progress WHERE customer_id = ?", (customer_id,))),
        "reminders": db.rows_to_list(conn.execute(
            "SELECT * FROM reminders WHERE customer_id = ?", (customer_id,))),
    }


def build_briefing(conn, days: int = 7, run_agent: bool = True) -> Dict[str, Any]:
    """The 'open the app' rundown. Optionally runs monitor+tick first."""
    ops = {"monitor": {}, "tick": {}}
    if run_agent:
        ops["monitor"] = monitor(conn, days=max(days, 14))
        ops["tick"] = tick(conn)

    custs = db.rows_to_list(conn.execute("SELECT * FROM customers"))

    new_customers = [c for c in custs if _within(c.get("created_at"), days)]

    ordered, order_revenue = [], 0.0
    in_town: List[Dict[str, Any]] = []
    next_steps: List[Dict[str, Any]] = []

    for c in custs:
        kids = _children(conn, c["id"])

        recent_orders = [o for o in kids["orders"] if _within(o.get("ordered_at"), days)]
        if recent_orders:
            rev = sum(float(o.get("amount") or 0) for o in recent_orders)
            order_revenue += rev
            ordered.append({"id": c["id"], "name": c["name"],
                            "orders": len(recent_orders), "revenue": round(rev, 2)})

        travel_posts = [a for a in kids["activities"]
                        if _within(a.get("occurred_at"), days)
                        and ai_agent.detect_travel(a.get("content", ""))]
        if travel_posts:
            in_town.append({"id": c["id"], "name": c["name"],
                            "post": travel_posts[0]["content"][:120],
                            "platform": travel_posts[0]["platform"]})

        h = scoring.compute_health(c, kids["orders"], kids["activities"],
                                   kids["interactions"], kids["progress"], kids["reminders"])
        if h["needs_support"]:
            next_steps.append({
                "id": c["id"], "name": c["name"], "score": h["score"],
                "urgency": h["suggested_urgency"], "channel": h["suggested_channel"],
                "action": f"{h['suggested_channel']} — {h['support_reason']}",
            })

    urgency_rank = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    next_steps.sort(key=lambda n: (urgency_rank.get(n["urgency"], 4), n["score"]))

    # Agent action counts in the window.
    def _count(kind: str, statuses: tuple) -> int:
        rows = db.rows_to_list(conn.execute(
            "SELECT created_at,status FROM agent_actions WHERE kind = ?", (kind,)))
        return sum(1 for r in rows
                   if r["status"] in statuses and _within(r["created_at"], days))

    posts_reviewed = _count("review", ("auto_done", "sent", "approved", "pending"))
    posts_replied = _count("reply", ("sent", "approved"))
    pending_approvals = conn.execute(
        "SELECT COUNT(*) AS c FROM agent_actions WHERE status = 'pending'").fetchone()["c"]

    stats = {
        "window_days": days,
        "total_customers": len(custs),
        "new_customers": len(new_customers),
        "ordered_customers": len(ordered),
        "order_revenue": round(order_revenue, 2),
        "in_town": len(in_town),
        "posts_reviewed": posts_reviewed,
        "posts_replied": posts_replied,
        "pending_approvals": pending_approvals,
    }

    data = {
        "stats": stats,
        "new_customers": [{"id": c["id"], "name": c["name"], "stage": c["stage"]} for c in new_customers],
        "ordered": ordered,
        "in_town": in_town,
        "next_steps": next_steps[:6],
        "ops": ops,
    }
    data["narration"] = ai_agent.narrate_briefing(data)
    data["ai_enabled"] = ai_agent.ai_available()
    return data

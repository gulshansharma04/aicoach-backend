"""
Deterministic customer-health scoring and support-need detection.

This is the rule-based "brain" that works with zero external dependencies.
The AI layer (ai_agent.py) builds *on top* of these signals to add natural
language, but the prioritization itself is fully deterministic, explainable,
and testable — so the distributor always gets a sensible answer, even offline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return now_utc().isoformat()


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def days_since(ts: Optional[str]) -> Optional[int]:
    dt = _parse(ts)
    if dt is None:
        return None
    return max(0, (now_utc() - dt).days)


def _max_date(items: List[Dict[str, Any]], field: str) -> Optional[str]:
    dates = [it.get(field) for it in items if it.get(field)]
    return max(dates) if dates else None


def compute_health(
    customer: Dict[str, Any],
    orders: List[Dict[str, Any]],
    activities: List[Dict[str, Any]],
    interactions: List[Dict[str, Any]],
    progress: List[Dict[str, Any]],
    reminders: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Return a health snapshot:
        {
          "score": int 0-100,
          "status": "healthy|nurture|at_risk",
          "reasons": [str, ...],          # why points were lost (explainability)
          "signals": {...},               # raw computed signals
          "needs_support": bool,
          "support_reason": str,
          "suggested_channel": "call|text",
          "suggested_urgency": "low|medium|high|urgent",
        }
    """
    score = 100
    reasons: List[str] = []

    # --- Last contact (interactions + outbound social we logged) ---
    last_interaction = _max_date(interactions, "occurred_at")
    d_contact = days_since(last_interaction)
    if d_contact is None:
        score -= 25
        reasons.append("No logged contact yet.")
    elif d_contact > 30:
        score -= 35
        reasons.append(f"No contact in {d_contact} days.")
    elif d_contact > 14:
        score -= 20
        reasons.append(f"Last contact was {d_contact} days ago.")

    # --- Orders / revenue recency ---
    last_order = _max_date(orders, "ordered_at")
    d_order = days_since(last_order)
    total_revenue = round(sum(float(o.get("amount") or 0) for o in orders), 2)
    if customer.get("stage") in ("active", "vip"):
        if d_order is None:
            score -= 15
            reasons.append("Active customer with no orders recorded.")
        elif d_order > 120:
            score -= 30
            reasons.append(f"No order in {d_order} days (reorder likely overdue).")
        elif d_order > 60:
            score -= 15
            reasons.append(f"Last order was {d_order} days ago.")

    # --- Social sentiment (recent negatives are a support signal) ---
    def _within(ts: Optional[str], window: int) -> bool:
        d = days_since(ts)
        return d is not None and d <= window

    recent_negative = [
        a for a in activities
        if a.get("sentiment") == "negative" and _within(a.get("occurred_at"), 21)
    ]
    if recent_negative:
        hit = min(30, 15 * len(recent_negative))
        score -= hit
        reasons.append(f"{len(recent_negative)} negative social signal(s) in last 3 weeks.")

    # --- Unanswered social comments ---
    unanswered = [a for a in activities if a.get("needs_response")]
    if unanswered:
        score -= min(15, 5 * len(unanswered))
        reasons.append(f"{len(unanswered)} social comment(s) awaiting a reply.")

    # --- Open support reminders ---
    open_support = [
        r for r in reminders
        if r.get("status") == "open" and r.get("kind") in ("support", "call", "text")
    ]
    if open_support:
        score -= 10
        reasons.append(f"{len(open_support)} open follow-up task(s).")

    # --- Stalled progress ---
    stalled = [p for p in progress if p.get("status") == "stalled"]
    if stalled:
        score -= 10
        reasons.append(f"{len(stalled)} progress goal(s) stalled.")

    score = max(0, min(100, score))

    if score >= 75:
        status = "healthy"
    elif score >= 50:
        status = "nurture"
    else:
        status = "at_risk"

    # --- Support-need detection ---
    needs_support = False
    support_reason = ""
    urgency = "low"

    if recent_negative:
        needs_support = True
        support_reason = "Recent negative social sentiment — reach out personally."
        urgency = "urgent"
    elif unanswered:
        needs_support = True
        support_reason = "Customer left a comment that still needs a response."
        urgency = "high"
    elif status == "at_risk":
        needs_support = True
        support_reason = "Health is at-risk — proactive outreach recommended."
        urgency = "high"
    elif d_contact is not None and d_contact > 21:
        needs_support = True
        support_reason = f"It's been {d_contact} days since you connected — time to check in."
        urgency = "medium"
    elif customer.get("stage") in ("active", "vip") and d_order is not None and d_order > 90:
        needs_support = True
        support_reason = "Likely due for a reorder — a friendly nudge could help."
        urgency = "medium"

    # Channel: negative/urgent → call (personal); otherwise customer preference.
    if urgency in ("urgent", "high"):
        suggested_channel = "call"
    else:
        suggested_channel = customer.get("preferred_channel") or "text"
    if suggested_channel not in ("call", "text"):
        suggested_channel = "text"

    return {
        "score": score,
        "status": status,
        "reasons": reasons,
        "signals": {
            "days_since_contact": d_contact,
            "days_since_order": d_order,
            "total_revenue": total_revenue,
            "order_count": len(orders),
            "recent_negative_count": len(recent_negative),
            "unanswered_comments": len(unanswered),
            "stalled_goals": len(stalled),
            "last_contact": last_interaction,
            "last_order": last_order,
        },
        "needs_support": needs_support,
        "support_reason": support_reason,
        "suggested_channel": suggested_channel,
        "suggested_urgency": urgency,
    }

"""
Customer Management AI Agent — FastAPI application.

A world-class CRM copilot for independent distributors:
  • Manage customers, their progress, orders, and social activity
  • Auto-score relationship health and detect who needs support
  • Generate personalized tips and ready-to-send call/text messages
  • Deliver a daily catch-up briefing and proactive reminders to reach out
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from . import database as db
from . import ai_agent, scoring, agent_ops
from . import connectors, auth, secret_store, notify
from .scoring import iso_now
from .models import (
    CustomerCreate, CustomerUpdate, ProgressCreate, OrderCreate, ActivityCreate,
    InteractionCreate, ReminderCreate, ReminderUpdate, ChatRequest, DraftRequest,
    PlanCreate, EnrollRequest, ActionDecision, ContentIdeaRequest,
    SignupRequest, VerifyRequest, ConsentUpdate, ConnectHerbalifeRequest,
    WebsiteRequest, LeadImportRequest, LeadQualifyRequest,
)


def _public_distributor(d: Dict[str, Any]) -> Dict[str, Any]:
    """Distributor view safe to return to the client (no secrets)."""
    return {
        "id": d["id"], "name": d.get("name", ""), "email": d.get("email"),
        "phone": d.get("phone"), "verified": bool(d.get("verified")),
        "onboarded": bool(d.get("onboarded")),
        "consent": d.get("consent") or {},
        "tracked_platforms": d.get("tracked_platforms") or [],
        "herbalife_connected": bool(d.get("herbalife_connected")),
    }


def _require_distributor(conn, token: Optional[str]) -> Dict[str, Any]:
    dist = auth.distributor_for_token(conn, token)
    if not dist:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return dist

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"

app = FastAPI(title="Customer Management AI Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    # Optional: seed demo customers on first boot (handy for cloud test deploys).
    if os.getenv("CRM_AUTOSEED") == "1":
        with db.get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) AS c FROM customers").fetchone()["c"]
        if n == 0:
            try:
                from .seed import seed
                seed()
            except Exception:
                pass


# ============================================================
# Internal helpers
# ============================================================

def _get_customer_row(conn, customer_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    cust = db.row_to_dict(row)
    if not cust:
        raise HTTPException(status_code=404, detail="Customer not found")
    return cust


def _children(conn, customer_id: int) -> Dict[str, List[Dict[str, Any]]]:
    return {
        "orders": db.rows_to_list(conn.execute(
            "SELECT * FROM orders WHERE customer_id = ? ORDER BY ordered_at DESC", (customer_id,))),
        "activities": db.rows_to_list(conn.execute(
            "SELECT * FROM activities WHERE customer_id = ? ORDER BY occurred_at DESC", (customer_id,))),
        "interactions": db.rows_to_list(conn.execute(
            "SELECT * FROM interactions WHERE customer_id = ? ORDER BY occurred_at DESC", (customer_id,))),
        "progress": db.rows_to_list(conn.execute(
            "SELECT * FROM progress WHERE customer_id = ? ORDER BY created_at DESC", (customer_id,))),
        "reminders": db.rows_to_list(conn.execute(
            "SELECT * FROM reminders WHERE customer_id = ? ORDER BY created_at DESC", (customer_id,))),
    }


def _health_for(cust: Dict[str, Any], kids: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    return scoring.compute_health(
        cust, kids["orders"], kids["activities"],
        kids["interactions"], kids["progress"], kids["reminders"],
    )


# ============================================================
# Health / meta
# ============================================================

@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "ai_enabled": ai_agent.ai_available()}


# ============================================================
# Customers
# ============================================================

@app.get("/api/customers")
def list_customers() -> Dict[str, Any]:
    with db.get_conn() as conn:
        custs = db.rows_to_list(conn.execute("SELECT * FROM customers ORDER BY name COLLATE NOCASE"))
        out = []
        for c in custs:
            kids = _children(conn, c["id"])
            h = _health_for(c, kids)
            out.append({
                **c,
                "health": h,
                "open_reminders": sum(1 for r in kids["reminders"] if r["status"] == "open"),
                "order_count": len(kids["orders"]),
                "total_revenue": h["signals"]["total_revenue"],
            })
    out.sort(key=lambda x: x["health"]["score"])  # neediest first
    return {"customers": out, "count": len(out)}


@app.post("/api/customers", status_code=201)
def create_customer(payload: CustomerCreate) -> Dict[str, Any]:
    import json
    now = iso_now()
    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO customers
               (name,email,phone,company,stage,tags,socials,notes,preferred_channel,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (payload.name, payload.email, payload.phone, payload.company, payload.stage,
             json.dumps(payload.tags), json.dumps(payload.socials), payload.notes,
             payload.preferred_channel, now, now),
        )
        cid = cur.lastrowid
    return get_customer(cid)


@app.get("/api/customers/{customer_id}")
def get_customer(customer_id: int) -> Dict[str, Any]:
    with db.get_conn() as conn:
        cust = _get_customer_row(conn, customer_id)
        kids = _children(conn, customer_id)
        health = _health_for(cust, kids)
    return {**cust, **kids, "health": health}


@app.patch("/api/customers/{customer_id}")
def update_customer(customer_id: int, payload: CustomerUpdate) -> Dict[str, Any]:
    import json
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return get_customer(customer_id)
    sets, vals = [], []
    for k, v in fields.items():
        if k in ("tags", "socials"):
            v = json.dumps(v)
        sets.append(f"{k} = ?")
        vals.append(v)
    sets.append("updated_at = ?")
    vals.append(iso_now())
    vals.append(customer_id)
    with db.get_conn() as conn:
        _get_customer_row(conn, customer_id)
        conn.execute(f"UPDATE customers SET {', '.join(sets)} WHERE id = ?", vals)
    return get_customer(customer_id)


@app.delete("/api/customers/{customer_id}")
def delete_customer(customer_id: int) -> Dict[str, Any]:
    with db.get_conn() as conn:
        _get_customer_row(conn, customer_id)
        conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
    return {"deleted": customer_id}


@app.post("/api/leads/import")
def import_leads(req: LeadImportRequest) -> Dict[str, Any]:
    """
    Turn pasted social handles / profile links / a comment thread into tracked
    leads. Set preview=true to parse only; pass an explicit `leads` list to
    create exactly those (e.g. after the user edits the preview).
    """
    import json
    leads = req.leads if req.leads else ai_agent.extract_leads(req.text, req.platform)

    if req.preview:
        return {"parsed": leads, "count": len(leads), "ai_enabled": ai_agent.ai_available()}

    now = iso_now()
    created, skipped = [], 0
    with db.get_conn() as conn:
        # Build the set of handles we already track on this platform (dedupe).
        existing = set()
        for c in db.rows_to_list(conn.execute("SELECT socials FROM customers")):
            h = (c.get("socials") or {}).get(req.platform, "")
            if h:
                existing.add(h.strip().lstrip("@").lower())

        for L in leads:
            handle = str(L.get("handle", "")).strip()
            key = handle.lstrip("@").lower()
            if not key or key in existing:
                skipped += 1
                continue
            existing.add(key)
            name = str(L.get("name") or handle).strip()
            note = str(L.get("note") or "").strip()
            priority = str(L.get("priority") or "").strip().lower()
            opener = str(L.get("opener") or "").strip()
            tags = list(req.tags) + ([priority] if priority in ("hot", "warm", "cold") else [])
            cur = conn.execute(
                """INSERT INTO customers
                   (name,email,phone,company,stage,tags,socials,notes,preferred_channel,source,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (name, None, None, "", req.stage,
                 json.dumps(tags), json.dumps({req.platform: handle}),
                 note, "social", f"{req.platform}_import", now, now))
            cid = cur.lastrowid
            # If qualified with a drafted opener, queue the first DM to send.
            if opener:
                pr = {"hot": "high", "warm": "medium", "cold": "low"}.get(priority, "medium")
                conn.execute(
                    """INSERT INTO reminders
                       (customer_id,kind,reason,draft_message,priority,status,due_date,source,created_at)
                       VALUES (?,?,?,?,?, 'open', ?, 'agent', ?)""",
                    (cid, "text", "Send first DM to new lead", opener, pr, now, now))
            # Hot leads get auto-enrolled into the fast follow-up cadence.
            enrolled = agent_ops.enroll_in_hot_plan(conn, cid) if priority == "hot" else False
            created.append({"id": cid, "name": name, "handle": handle,
                            "priority": priority, "auto_enrolled": enrolled})

    auto_enrolled = sum(1 for x in created if x.get("auto_enrolled"))
    return {"created": created, "created_count": len(created), "skipped": skipped,
            "auto_enrolled": auto_enrolled, "ai_enabled": ai_agent.ai_available()}


_PRIORITY_RANK = {"hot": 0, "warm": 1, "cold": 2}


@app.post("/api/leads/qualify")
def qualify_leads(req: LeadQualifyRequest) -> Dict[str, Any]:
    """Score each lead's intent and draft a first DM. Returned hottest-first."""
    with db.get_conn() as conn:
        lang = agent_ops.get_language(conn)
    out = []
    for L in req.leads:
        q = ai_agent.qualify_lead(
            str(L.get("name", "")), str(L.get("handle", "")),
            str(L.get("note", "")), req.platform, lang=lang)
        out.append({**L, **q})
    out.sort(key=lambda x: (_PRIORITY_RANK.get(x["priority"], 3), -x["score"]))
    return {"leads": out, "count": len(out), "ai_enabled": ai_agent.ai_available()}


@app.post("/api/customers/{customer_id}/qualify")
def qualify_customer(customer_id: int) -> Dict[str, Any]:
    """Qualify an existing lead from their latest social comment, and queue a first DM."""
    with db.get_conn() as conn:
        cust = _get_customer_row(conn, customer_id)
        acts = db.rows_to_list(conn.execute(
            "SELECT * FROM activities WHERE customer_id = ? ORDER BY occurred_at DESC LIMIT 1",
            (customer_id,)))
        comment = (acts[0]["content"] if acts else "") or cust.get("notes", "")
        platform = (list((cust.get("socials") or {}).keys()) or ["instagram"])[0]
        handle = (cust.get("socials") or {}).get(platform, "")
        q = ai_agent.qualify_lead(cust["name"], handle, comment, platform,
                                  lang=agent_ops.get_language(conn))
        pr = {"hot": "high", "warm": "medium", "cold": "low"}.get(q["priority"], "medium")
        existing = conn.execute(
            "SELECT id FROM reminders WHERE customer_id=? AND reason='Send first DM to new lead' AND status='open'",
            (customer_id,)).fetchone()
        if not existing:
            conn.execute(
                """INSERT INTO reminders
                   (customer_id,kind,reason,draft_message,priority,status,due_date,source,created_at)
                   VALUES (?,?,?,?,?, 'open', ?, 'agent', ?)""",
                (customer_id, "text", "Send first DM to new lead", q["opener"], pr, iso_now(), iso_now()))
        auto_enrolled = agent_ops.enroll_in_hot_plan(conn, customer_id) if q["priority"] == "hot" else False
    return {"customer_id": customer_id, **q, "auto_enrolled": auto_enrolled,
            "ai_enabled": ai_agent.ai_available()}


# ============================================================
# Sub-resources
# ============================================================

@app.post("/api/customers/{customer_id}/progress", status_code=201)
def add_progress(customer_id: int, p: ProgressCreate) -> Dict[str, Any]:
    with db.get_conn() as conn:
        _get_customer_row(conn, customer_id)
        conn.execute(
            "INSERT INTO progress (customer_id,title,status,note,created_at) VALUES (?,?,?,?,?)",
            (customer_id, p.title, p.status, p.note, iso_now()),
        )
    return get_customer(customer_id)


@app.post("/api/customers/{customer_id}/orders", status_code=201)
def add_order(customer_id: int, o: OrderCreate) -> Dict[str, Any]:
    with db.get_conn() as conn:
        _get_customer_row(conn, customer_id)
        conn.execute(
            "INSERT INTO orders (customer_id,product,amount,status,ordered_at,notes) VALUES (?,?,?,?,?,?)",
            (customer_id, o.product, o.amount, o.status, o.ordered_at or iso_now(), o.notes),
        )
    return get_customer(customer_id)


@app.post("/api/customers/{customer_id}/activities", status_code=201)
def add_activity(customer_id: int, a: ActivityCreate) -> Dict[str, Any]:
    sentiment = a.sentiment or ai_agent.detect_sentiment(a.content)
    with db.get_conn() as conn:
        _get_customer_row(conn, customer_id)
        conn.execute(
            """INSERT INTO activities
               (customer_id,platform,kind,content,sentiment,url,needs_response,occurred_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (customer_id, a.platform, a.kind, a.content, sentiment, a.url,
             1 if a.needs_response else 0, a.occurred_at or iso_now()),
        )
    return get_customer(customer_id)


@app.post("/api/customers/{customer_id}/interactions", status_code=201)
def add_interaction(customer_id: int, i: InteractionCreate) -> Dict[str, Any]:
    with db.get_conn() as conn:
        _get_customer_row(conn, customer_id)
        conn.execute(
            "INSERT INTO interactions (customer_id,channel,summary,occurred_at) VALUES (?,?,?,?)",
            (customer_id, i.channel, i.summary, i.occurred_at or iso_now()),
        )
    return get_customer(customer_id)


# ============================================================
# Reminders
# ============================================================

@app.get("/api/reminders")
def list_reminders(status: Optional[str] = "open") -> Dict[str, Any]:
    q = """SELECT r.*, c.name AS customer_name, c.preferred_channel
           FROM reminders r JOIN customers c ON c.id = r.customer_id"""
    params: List[Any] = []
    if status and status != "all":
        q += " WHERE r.status = ?"
        params.append(status)
    q += " ORDER BY CASE r.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 " \
         "WHEN 'medium' THEN 2 ELSE 3 END, r.created_at DESC"
    with db.get_conn() as conn:
        rows = db.rows_to_list(conn.execute(q, params))
    return {"reminders": rows, "count": len(rows)}


@app.post("/api/customers/{customer_id}/reminders", status_code=201)
def add_reminder(customer_id: int, r: ReminderCreate) -> Dict[str, Any]:
    with db.get_conn() as conn:
        _get_customer_row(conn, customer_id)
        conn.execute(
            """INSERT INTO reminders
               (customer_id,kind,reason,draft_message,priority,status,due_date,source,created_at)
               VALUES (?,?,?,?,?, 'open', ?, 'manual', ?)""",
            (customer_id, r.kind, r.reason, r.draft_message, r.priority, r.due_date, iso_now()),
        )
    return get_customer(customer_id)


@app.patch("/api/reminders/{reminder_id}")
def update_reminder(reminder_id: int, payload: ReminderUpdate) -> Dict[str, Any]:
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    sets = [f"{k} = ?" for k in fields]
    vals = list(fields.values()) + [reminder_id]
    with db.get_conn() as conn:
        exists = conn.execute("SELECT id FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Reminder not found")
        conn.execute(f"UPDATE reminders SET {', '.join(sets)} WHERE id = ?", vals)
        row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
    return db.row_to_dict(row)


# ============================================================
# AI Agent
# ============================================================

@app.get("/api/customers/{customer_id}/tips")
def customer_tips(customer_id: int) -> Dict[str, Any]:
    with db.get_conn() as conn:
        cust = _get_customer_row(conn, customer_id)
        kids = _children(conn, customer_id)
        health = _health_for(cust, kids)
    with db.get_conn() as conn:
        lang = agent_ops.get_language(conn)
    tips = ai_agent.generate_tips(cust, health, kids["activities"], lang=lang)
    return {"customer_id": customer_id, "tips": tips, "ai_enabled": ai_agent.ai_available()}


@app.post("/api/customers/{customer_id}/draft")
def customer_draft(customer_id: int, req: DraftRequest) -> Dict[str, Any]:
    with db.get_conn() as conn:
        cust = _get_customer_row(conn, customer_id)
        kids = _children(conn, customer_id)
        health = _health_for(cust, kids)
        lang = agent_ops.get_language(conn)
    channel = req.channel or health["suggested_channel"]
    message = ai_agent.draft_message(cust, health, channel, req.goal, lang=lang)
    return {"customer_id": customer_id, "channel": channel, "message": message,
            "ai_enabled": ai_agent.ai_available()}


@app.get("/api/agent/digest")
def agent_digest(auto_create_reminders: bool = True) -> Dict[str, Any]:
    """
    The daily catch-up. Scans every customer, ranks who needs attention, drafts
    an outreach message for each, and (optionally) files proactive reminders so
    nothing slips through the cracks.
    """
    focus: List[Dict[str, Any]] = []
    totals = {"customers": 0, "needs_attention": 0, "urgent": 0,
              "healthy": 0, "nurture": 0, "at_risk": 0, "reminders_created": 0}

    with db.get_conn() as conn:
        custs = db.rows_to_list(conn.execute("SELECT * FROM customers"))
        totals["customers"] = len(custs)

        enriched = []
        for c in custs:
            kids = _children(conn, c["id"])
            h = _health_for(c, kids)
            totals[h["status"]] = totals.get(h["status"], 0) + 1
            enriched.append((c, kids, h))

        # Rank: needs-support first, then urgency, then lowest health.
        urgency_rank = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
        enriched.sort(key=lambda t: (
            0 if t[2]["needs_support"] else 1,
            urgency_rank.get(t[2]["suggested_urgency"], 4),
            t[2]["score"],
        ))

        for c, kids, h in enriched:
            if not h["needs_support"]:
                continue
            totals["needs_attention"] += 1
            if h["suggested_urgency"] == "urgent":
                totals["urgent"] += 1

            channel = h["suggested_channel"]
            message = ai_agent.draft_message(c, h, channel, h["support_reason"] or "check in")

            if auto_create_reminders:
                existing = conn.execute(
                    "SELECT id FROM reminders WHERE customer_id = ? AND source = 'agent' AND status = 'open'",
                    (c["id"],),
                ).fetchone()
                if not existing:
                    conn.execute(
                        """INSERT INTO reminders
                           (customer_id,kind,reason,draft_message,priority,status,due_date,source,created_at)
                           VALUES (?,?,?,?,?, 'open', ?, 'agent', ?)""",
                        (c["id"], channel if channel in ("call", "text") else "follow_up",
                         h["support_reason"], message, h["suggested_urgency"],
                         iso_now(), iso_now()),
                    )
                    totals["reminders_created"] += 1

            focus.append({
                "customer_id": c["id"],
                "name": c["name"],
                "preferred_channel": c["preferred_channel"],
                "health": h,
                "suggested_channel": channel,
                "draft_message": message,
            })

    briefing = ai_agent.daily_briefing(focus, totals)
    return {"briefing": briefing, "totals": totals, "focus": focus,
            "ai_enabled": ai_agent.ai_available()}


@app.post("/api/agent/chat")
def agent_chat(req: ChatRequest) -> Dict[str, Any]:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    with db.get_conn() as conn:
        if req.customer_id:
            cust = _get_customer_row(conn, req.customer_id)
            kids = _children(conn, req.customer_id)
            h = _health_for(cust, kids)
            context = (
                f"Single customer focus: {cust['name']} "
                f"(stage {cust['stage']}, channel {cust['preferred_channel']}).\n"
                f"Health {h['score']}/100 ({h['status']}). Reasons: {h['reasons']}.\n"
                f"Signals: {h['signals']}.\n"
                f"Recent orders: {[{'product': o['product'], 'amount': o['amount'], 'status': o['status']} for o in kids['orders'][:5]]}\n"
                f"Recent social: {[{'platform': a['platform'], 'kind': a['kind'], 'content': a['content'], 'sentiment': a['sentiment']} for a in kids['activities'][:5]]}\n"
                f"Notes: {cust['notes']}"
            )
        else:
            custs = db.rows_to_list(conn.execute("SELECT * FROM customers"))
            lines = []
            for c in custs:
                kids = _children(conn, c["id"])
                h = _health_for(c, kids)
                lines.append(
                    f"- {c['name']}: {h['score']}/100 ({h['status']}), "
                    f"{'NEEDS SUPPORT: ' + h['support_reason'] if h['needs_support'] else 'ok'}; "
                    f"channel {h['suggested_channel']}"
                )
            context = "Customer portfolio overview:\n" + ("\n".join(lines) if lines else "No customers yet.")
        lang = agent_ops.get_language(conn)

    answer = ai_agent.agent_chat(req.message, context, lang=lang)
    return {"answer": answer, "ai_enabled": ai_agent.ai_available()}


# ============================================================
# Onboarding: auth, 2FA, consent, connections
# ============================================================

def _token(authorization: Optional[str], x_distributor_token: Optional[str]) -> Optional[str]:
    if x_distributor_token:
        return x_distributor_token
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:]
    return None


@app.post("/api/auth/signup")
def auth_signup(payload: SignupRequest) -> Dict[str, Any]:
    with db.get_conn() as conn:
        try:
            return auth.signup_or_login(conn, payload.name, payload.email, payload.phone)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/auth/verify")
def auth_verify(payload: VerifyRequest) -> Dict[str, Any]:
    with db.get_conn() as conn:
        try:
            res = auth.verify_otp(conn, payload.distributor_id, payload.code)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        dist = db.row_to_dict(conn.execute(
            "SELECT * FROM distributors WHERE id=?", (payload.distributor_id,)).fetchone())
    res["distributor"] = _public_distributor(dist)
    return res


@app.get("/api/me")
def me(authorization: Optional[str] = Header(None),
       x_distributor_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    with db.get_conn() as conn:
        dist = _require_distributor(conn, _token(authorization, x_distributor_token))
    return _public_distributor(dist)


@app.patch("/api/me/consent")
def update_consent(payload: ConsentUpdate,
                   authorization: Optional[str] = Header(None),
                   x_distributor_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    import json
    with db.get_conn() as conn:
        dist = _require_distributor(conn, _token(authorization, x_distributor_token))
        sets, vals = [], []
        if payload.consent is not None:
            merged = dict(agent_ops.CONSENT_DEFAULTS)
            merged.update(dist.get("consent") or {})
            merged.update(payload.consent)
            sets.append("consent = ?"); vals.append(json.dumps(merged))
        if payload.tracked_platforms is not None:
            sets.append("tracked_platforms = ?"); vals.append(json.dumps(payload.tracked_platforms))
        if payload.name is not None:
            sets.append("name = ?"); vals.append(payload.name)
        if payload.onboarded is not None:
            sets.append("onboarded = ?"); vals.append(1 if payload.onboarded else 0)
        if sets:
            sets.append("updated_at = ?"); vals.append(iso_now())
            vals.append(dist["id"])
            conn.execute(f"UPDATE distributors SET {', '.join(sets)} WHERE id = ?", vals)
        dist = db.row_to_dict(conn.execute(
            "SELECT * FROM distributors WHERE id=?", (dist["id"],)).fetchone())
    return _public_distributor(dist)


@app.post("/api/connect/herbalife")
def connect_herbalife(payload: ConnectHerbalifeRequest,
                      authorization: Optional[str] = Header(None),
                      x_distributor_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    """
    Store myHerbalife credentials in the secret store (NEVER the DB) so the
    agent can pull customer details. The DB only records that a connection
    exists. Credentials are used solely to identify the distributor's own
    customers and are not shared.
    """
    with db.get_conn() as conn:
        dist = _require_distributor(conn, _token(authorization, x_distributor_token))
        secret_store.store_credentials(dist["id"], "herbalife", {
            "username": payload.username,
            "password": payload.password,
            "portal_url": payload.portal_url or "",
        })
        conn.execute(
            "UPDATE distributors SET herbalife_connected=1, herbalife_connected_at=?, updated_at=? WHERE id=?",
            (iso_now(), iso_now(), dist["id"]))
    return {"ok": True, "herbalife_connected": True,
            "message": "Connected. Your credentials are stored securely and never shared."}


@app.post("/api/connect/herbalife/revoke")
def revoke_herbalife(authorization: Optional[str] = Header(None),
                     x_distributor_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    with db.get_conn() as conn:
        dist = _require_distributor(conn, _token(authorization, x_distributor_token))
        secret_store.revoke_credentials(dist["id"], "herbalife")
        conn.execute("UPDATE distributors SET herbalife_connected=0, updated_at=? WHERE id=?",
                     (iso_now(), dist["id"]))
    return {"ok": True, "herbalife_connected": False}


# ============================================================
# Communication plans & enrollments
# ============================================================

@app.get("/api/plans")
def list_plans() -> Dict[str, Any]:
    with db.get_conn() as conn:
        return {"plans": agent_ops.list_plans(conn)}


@app.post("/api/plans", status_code=201)
def create_plan(payload: PlanCreate) -> Dict[str, Any]:
    with db.get_conn() as conn:
        pid = agent_ops.create_plan(
            conn, payload.name, payload.description,
            [s.model_dump() for s in payload.steps])
        plans = agent_ops.list_plans(conn)
    return next((p for p in plans if p["id"] == pid), {"id": pid})


@app.post("/api/enrollments", status_code=201)
def enroll(payload: EnrollRequest) -> Dict[str, Any]:
    with db.get_conn() as conn:
        try:
            return agent_ops.enroll(conn, payload.customer_id, payload.plan_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/customers/{customer_id}/enrollments")
def customer_enrollments(customer_id: int) -> Dict[str, Any]:
    with db.get_conn() as conn:
        rows = db.rows_to_list(conn.execute(
            """SELECT e.*, p.name AS plan_name FROM enrollments e
               JOIN comm_plans p ON p.id = e.plan_id
               WHERE e.customer_id = ? ORDER BY e.started_at DESC""", (customer_id,)))
    return {"enrollments": rows}


# ============================================================
# Autonomous operations: tick, monitor, actions, briefing
# ============================================================

@app.post("/api/agent/tick")
def agent_tick() -> Dict[str, Any]:
    """Advance all due communication-plan steps (call from a scheduler/cron)."""
    with db.get_conn() as conn:
        return agent_ops.tick(conn)


@app.post("/api/agent/monitor")
def agent_monitor(days: int = 14) -> Dict[str, Any]:
    """Review recent social activity and apply the autonomy policy."""
    with db.get_conn() as conn:
        return agent_ops.monitor(conn, days=days)


@app.get("/api/agent/briefing")
def agent_briefing(days: int = 7, run_agent: bool = True) -> Dict[str, Any]:
    """The morning 'open the app' rundown (text + voice narration)."""
    with db.get_conn() as conn:
        return agent_ops.build_briefing(conn, days=days, run_agent=run_agent)


@app.get("/api/agent/actions")
def list_actions(status: Optional[str] = "pending") -> Dict[str, Any]:
    q = """SELECT a.*, c.name AS customer_name FROM agent_actions a
           JOIN customers c ON c.id = a.customer_id"""
    params: List[Any] = []
    if status and status != "all":
        q += " WHERE a.status = ?"
        params.append(status)
    q += " ORDER BY a.created_at DESC LIMIT 200"
    with db.get_conn() as conn:
        rows = db.rows_to_list(conn.execute(q, params))
    return {"actions": rows, "count": len(rows)}


@app.patch("/api/agent/actions/{action_id}")
def decide_action(action_id: int, payload: ActionDecision) -> Dict[str, Any]:
    """Approve (send) or reject a queued sensitive action."""
    new_status = "approved" if payload.decision == "approve" else "rejected"
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM agent_actions WHERE id = ?", (action_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Action not found")
        action = db.row_to_dict(row)
        draft = payload.edited_draft if payload.edited_draft is not None else action["draft"]
        conn.execute("UPDATE agent_actions SET status = ?, draft = ? WHERE id = ?",
                     (new_status, draft, action_id))
        if new_status == "approved":
            # record the outreach as a real interaction
            conn.execute(
                "INSERT INTO interactions (customer_id,channel,summary,occurred_at) VALUES (?,?,?,?)",
                (action["customer_id"], action["channel"] or "social",
                 f"[approved] {action['summary']}", iso_now()))
        row = conn.execute("SELECT * FROM agent_actions WHERE id = ?", (action_id,)).fetchone()
    return db.row_to_dict(row)


# ============================================================
# External connectors (Herbalife, social) — scaffolding
# ============================================================

@app.get("/api/connectors")
def connectors_status() -> Dict[str, Any]:
    return connectors.status()


@app.post("/api/connectors/herbalife/sync")
def herbalife_sync() -> Dict[str, Any]:
    with db.get_conn() as conn:
        return connectors.sync_herbalife(conn)


@app.post("/api/connectors/social/sync")
def social_sync(days: int = 14) -> Dict[str, Any]:
    with db.get_conn() as conn:
        return connectors.sync_social(conn, days=days)


# ============================================================
# Voice (realistic neural TTS)
# ============================================================

class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = None


@app.post("/api/voice/tts")
def voice_tts(req: TTSRequest):
    """Return MP3 audio for the text using a neural voice (falls back to browser TTS if unavailable)."""
    from fastapi.responses import Response
    audio = ai_agent.synthesize_speech(req.text, req.voice)
    if not audio:
        return JSONResponse({"available": False,
                             "message": "Neural TTS needs OPENAI_API_KEY; using device voice instead."},
                            status_code=200)
    return Response(content=audio, media_type="audio/mpeg")


# ============================================================
# Notifications & email monitor
# ============================================================

@app.get("/api/notifications")
def get_notifications(unread_only: bool = False) -> Dict[str, Any]:
    with db.get_conn() as conn:
        items = notify.list_notifications(conn, unread_only=unread_only)
        unread = sum(1 for n in items if not n["read"])
    return {"notifications": items, "unread": unread,
            "push_configured": notify.push_configured()}


@app.post("/api/notifications/{notification_id}/read")
def read_notification(notification_id: int) -> Dict[str, Any]:
    with db.get_conn() as conn:
        notify.mark_read(conn, notification_id)
    return {"ok": True}


@app.post("/api/notifications/read-all")
def read_all_notifications() -> Dict[str, Any]:
    with db.get_conn() as conn:
        n = notify.mark_all_read(conn)
    return {"ok": True, "marked": n}


@app.post("/api/agent/scan-email")
def scan_email() -> Dict[str, Any]:
    """Scan subscribed emails, triage importance, and notify about what matters."""
    scan = connectors.scan_email()
    triaged, created = [], 0
    with db.get_conn() as conn:
        for em in scan.get("emails", []):
            t = ai_agent.triage_email(em.get("subject", ""), em.get("body", ""))
            triaged.append({**em, **t})
            if t["important"]:
                notify.notify(conn, f"📧 {t['summary']}", t["reason"],
                              level=t["level"], source="email")
                created += 1
    return {"configured": scan.get("configured", False),
            "message": scan.get("message", ""), "scanned": len(triaged),
            "notified": created, "emails": triaged}


# ============================================================
# Website builder (Google Stitch)
# ============================================================

@app.post("/api/website/plan")
def website_plan(req: WebsiteRequest) -> Dict[str, Any]:
    brief = ai_agent.website_plan(req.product, req.goal, req.brand_voice)
    result: Dict[str, Any] = {"brief": brief, "ai_enabled": ai_agent.ai_available()}
    if req.build:
        result["build"] = connectors.create_site_with_stitch(brief)
    return result


# ============================================================
# Content inspiration (repost ideas from CEO / brand news)
# ============================================================

@app.get("/api/content/ceo-feed")
def ceo_feed(limit: int = 5) -> Dict[str, Any]:
    """Latest posts about the Herbalife CEO to inspire reposts."""
    return connectors.fetch_ceo_posts(limit=limit)


@app.post("/api/content/ideas")
def content_ideas(req: ContentIdeaRequest) -> Dict[str, Any]:
    with db.get_conn() as conn:
        lang = agent_ops.get_language(conn)
    ideas = ai_agent.content_ideas(req.topic, req.source_post, req.platforms, req.n, lang=lang)
    return {"topic": req.topic, "ideas": ideas, "ai_enabled": ai_agent.ai_available()}


# ============================================================
# Static web app (served last so /api/* wins)
# ============================================================

if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
else:
    @app.get("/")
    def root() -> JSONResponse:
        return JSONResponse({"service": "Customer Management AI Agent", "docs": "/docs"})

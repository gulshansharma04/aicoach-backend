"""
Seed the database with realistic sample data so the app is demo-ready.

Run:  python -m customer_agent.app.seed
(Use ``--reset`` to wipe existing rows first.)
"""

from __future__ import annotations

import sys
import json
from datetime import timedelta

from . import database as db
from .scoring import now_utc


def _ago(days: int) -> str:
    return (now_utc() - timedelta(days=days)).isoformat()


CUSTOMERS = [
    {
        "name": "Maria Gonzalez", "email": "maria.g@example.com", "phone": "+1-555-0101",
        "company": "", "stage": "vip", "preferred_channel": "call",
        "tags": ["wellness", "referral-source"],
        "socials": {"instagram": "@maria_glow", "facebook": "maria.gonzalez"},
        "notes": "Top customer and great referrer. Loves the energy line.",
        "orders": [{"product": "Energy Bundle", "amount": 220.0, "days": 5, "status": "delivered"},
                   {"product": "Skincare Set", "amount": 145.0, "days": 40, "status": "delivered"}],
        "activities": [{"platform": "instagram", "kind": "comment",
                        "content": "I absolutely love these products, recommend to everyone!", "days": 3}],
        "interactions": [{"channel": "call", "summary": "Checked in, she's thrilled.", "days": 4}],
        "progress": [{"title": "Become a brand ambassador", "status": "in_progress", "note": "Sharing weekly."}],
    },
    {
        "name": "Derek Lin", "email": "derek.lin@example.com", "phone": "+1-555-0102",
        "company": "Lin Fitness", "stage": "active", "preferred_channel": "text",
        "tags": ["fitness"],
        "socials": {"instagram": "@dereklifts", "tiktok": "@dereklifts"},
        "notes": "Runs a small gym, buys protein in bulk.",
        "orders": [{"product": "Protein x6", "amount": 180.0, "days": 95, "status": "delivered"}],
        "activities": [{"platform": "tiktok", "kind": "mention",
                        "content": "Shipment was late and I was pretty disappointed honestly.", "days": 6,
                        "needs_response": True}],
        "interactions": [{"channel": "text", "summary": "Confirmed last bulk order.", "days": 95}],
        "progress": [{"title": "Set up monthly auto-order", "status": "stalled", "note": "Hasn't confirmed."}],
    },
    {
        "name": "Priya Patel", "email": "priya.p@example.com", "phone": "+1-555-0103",
        "company": "", "stage": "active", "preferred_channel": "text",
        "tags": ["skincare", "new"],
        "socials": {"instagram": "@priya.skincare"},
        "notes": "New customer, very engaged on social.",
        "orders": [{"product": "Starter Kit", "amount": 65.0, "days": 18, "status": "delivered"}],
        "activities": [{"platform": "instagram", "kind": "comment",
                        "content": "How often should I use the night serum?", "days": 1,
                        "needs_response": True}],
        "interactions": [{"channel": "text", "summary": "Welcomed her, sent starter guide.", "days": 18}],
        "progress": [{"title": "Complete 30-day routine", "status": "in_progress", "note": "Day 18."}],
    },
    {
        "name": "James O'Connor", "email": "james.o@example.com", "phone": "+1-555-0104",
        "company": "", "stage": "active", "preferred_channel": "call",
        "tags": ["loyal"],
        "socials": {"facebook": "james.oconnor"},
        "notes": "Reliable monthly buyer — but quiet lately.",
        "orders": [{"product": "Daily Greens", "amount": 55.0, "days": 130, "status": "delivered"}],
        "activities": [],
        "interactions": [{"channel": "call", "summary": "Reordered greens.", "days": 130}],
        "progress": [],
    },
    {
        "name": "Aisha Bello", "email": "aisha.b@example.com", "phone": "+1-555-0105",
        "company": "", "stage": "prospect", "preferred_channel": "text",
        "tags": ["lead"],
        "socials": {"instagram": "@aisha.b"},
        "notes": "Asked about the weight-management line at an event.",
        "orders": [],
        "activities": [{"platform": "instagram", "kind": "dm",
                        "content": "Hi! Can you tell me more about the plan?", "days": 2, "needs_response": True}],
        "interactions": [],
        "progress": [{"title": "Send product overview", "status": "planned", "note": ""}],
    },
    {
        "name": "Tom Becker", "email": "tom.becker@example.com", "phone": "+1-555-0106",
        "company": "", "stage": "active", "preferred_channel": "text",
        "tags": ["sleep"],
        "socials": {"facebook": "tom.becker"},
        "notes": "Happy customer, steady.",
        "orders": [{"product": "Sleep Support", "amount": 48.0, "days": 22, "status": "delivered"}],
        "activities": [{"platform": "facebook", "kind": "review",
                        "content": "Great product, sleeping so much better. Thank you!", "days": 10}],
        "interactions": [{"channel": "text", "summary": "Checked in, all good.", "days": 12}],
        "progress": [],
    },
]


def reset() -> None:
    with db.get_conn() as conn:
        for t in ("reminders", "interactions", "activities", "orders", "progress", "customers"):
            conn.execute(f"DELETE FROM {t}")


def seed() -> None:
    db.init_db()
    with db.get_conn() as conn:
        for c in CUSTOMERS:
            cur = conn.execute(
                """INSERT INTO customers
                   (name,email,phone,company,stage,tags,socials,notes,preferred_channel,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (c["name"], c["email"], c["phone"], c["company"], c["stage"],
                 json.dumps(c["tags"]), json.dumps(c["socials"]), c["notes"],
                 c["preferred_channel"], _ago(180), _ago(0)),
            )
            cid = cur.lastrowid
            for o in c.get("orders", []):
                conn.execute(
                    "INSERT INTO orders (customer_id,product,amount,status,ordered_at,notes) VALUES (?,?,?,?,?,'')",
                    (cid, o["product"], o["amount"], o.get("status", "paid"), _ago(o["days"])),
                )
            for a in c.get("activities", []):
                from .ai_agent import detect_sentiment
                conn.execute(
                    """INSERT INTO activities
                       (customer_id,platform,kind,content,sentiment,url,needs_response,occurred_at)
                       VALUES (?,?,?,?,?,'',?,?)""",
                    (cid, a["platform"], a["kind"], a["content"],
                     a.get("sentiment") or detect_sentiment(a["content"]),
                     1 if a.get("needs_response") else 0, _ago(a["days"])),
                )
            for i in c.get("interactions", []):
                conn.execute(
                    "INSERT INTO interactions (customer_id,channel,summary,occurred_at) VALUES (?,?,?,?)",
                    (cid, i["channel"], i["summary"], _ago(i["days"])),
                )
            for p in c.get("progress", []):
                conn.execute(
                    "INSERT INTO progress (customer_id,title,status,note,created_at) VALUES (?,?,?,?,?)",
                    (cid, p["title"], p["status"], p["note"], _ago(20)),
                )
    print(f"Seeded {len(CUSTOMERS)} customers into {db.DB_PATH}")


if __name__ == "__main__":
    if "--reset" in sys.argv:
        db.init_db()
        reset()
        print("Existing data wiped.")
    seed()

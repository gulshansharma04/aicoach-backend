"""
Lightweight distributor auth: signup with email OR phone, 2-factor via OTP,
and opaque session tokens.

Scope note: this is a pragmatic, single-service auth suitable for the agent's
onboarding. OTP delivery (SMS/email) requires a provider (Twilio/SES) and its
credentials on the remote infra; until then we run in DEV mode where the code
is returned by the API so the flow is testable end-to-end. Set
``CRM_DEV_OTP=0`` to require a real delivery integration.
"""

from __future__ import annotations

import os
import hashlib
import secrets
from datetime import timedelta
from typing import Any, Dict, Optional

from . import database as db
from .scoring import iso_now, now_utc, _parse  # type: ignore

OTP_TTL_MINUTES = 10
DEV_OTP = os.getenv("CRM_DEV_OTP", "1") == "1"


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _gen_otp() -> str:
    return f"{secrets.randbelow(900000) + 100000}"  # 6 digits


def _find_distributor(conn, email: Optional[str], phone: Optional[str]) -> Optional[Dict[str, Any]]:
    if email:
        r = conn.execute("SELECT * FROM distributors WHERE email = ?", (email,)).fetchone()
        if r:
            return db.row_to_dict(r)
    if phone:
        r = conn.execute("SELECT * FROM distributors WHERE phone = ?", (phone,)).fetchone()
        if r:
            return db.row_to_dict(r)
    return None


def signup_or_login(conn, name: str, email: Optional[str], phone: Optional[str]) -> Dict[str, Any]:
    """Create (or find) a distributor and issue an OTP challenge."""
    if not email and not phone:
        raise ValueError("Provide an email or phone number")

    dist = _find_distributor(conn, email, phone)
    now = iso_now()
    if not dist:
        cur = conn.execute(
            """INSERT INTO distributors (name,email,phone,verified,onboarded,created_at,updated_at)
               VALUES (?,?,?,0,0,?,?)""",
            (name or "", email, phone, now, now))
        did = cur.lastrowid
    else:
        did = dist["id"]
        if name and not dist.get("name"):
            conn.execute("UPDATE distributors SET name=?, updated_at=? WHERE id=?", (name, now, did))

    code = _gen_otp()
    expires = (now_utc() + timedelta(minutes=OTP_TTL_MINUTES)).isoformat()
    conn.execute(
        "INSERT INTO otp_codes (distributor_id,code_hash,purpose,expires_at,used,created_at) VALUES (?,?,?,?,0,?)",
        (did, _hash(code), "verify", expires, now))

    out: Dict[str, Any] = {
        "distributor_id": did,
        "channel": "email" if email else "sms",
        "destination": email or phone,
        "message": "We sent you a 6-digit verification code.",
        "dev_mode": DEV_OTP,
    }
    # In DEV mode we surface the code so the flow is testable without a provider.
    if DEV_OTP:
        out["dev_otp"] = code
    return out


def verify_otp(conn, distributor_id: int, code: str) -> Dict[str, Any]:
    """Check the OTP; on success mark verified and issue a session token."""
    rows = db.rows_to_list(conn.execute(
        "SELECT * FROM otp_codes WHERE distributor_id=? AND used=0 ORDER BY id DESC", (distributor_id,)))
    match = None
    for r in rows:
        exp = _parse(r["expires_at"])
        if exp and exp >= now_utc() and r["code_hash"] == _hash(code):
            match = r
            break
    if not match:
        raise ValueError("Invalid or expired code")

    conn.execute("UPDATE otp_codes SET used=1 WHERE id=?", (match["id"],))
    conn.execute("UPDATE distributors SET verified=1, updated_at=? WHERE id=?",
                 (iso_now(), distributor_id))

    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions (token,distributor_id,created_at) VALUES (?,?,?)",
                 (token, distributor_id, iso_now()))
    return {"token": token, "distributor_id": distributor_id}


def distributor_for_token(conn, token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    row = conn.execute("SELECT distributor_id FROM sessions WHERE token=?", (token,)).fetchone()
    if not row:
        return None
    return db.row_to_dict(conn.execute(
        "SELECT * FROM distributors WHERE id=?", (row["distributor_id"],)).fetchone())

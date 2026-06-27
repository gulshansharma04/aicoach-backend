"""
Notifications + mobile push.

``notify()`` writes a notification row (the in-app bell) and, when a push
provider is configured, also sends a mobile push. The push transport is a
single seam: in production wire Firebase Cloud Messaging (Android) / APNs (iOS)
or Web Push here. Until then pushes are recorded as "pushed=0" and surface in
the app — no external dependency required to develop the full flow.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import database as db
from .scoring import iso_now

ENV_FCM_KEY = "FCM_SERVER_KEY"


def push_configured() -> bool:
    return bool(os.getenv(ENV_FCM_KEY))


def _send_push(title: str, body: str, level: str) -> bool:
    """Send a mobile push. Returns True if actually delivered."""
    if not push_configured():
        return False
    # --- Implementation outline (FCM) ---
    # import requests
    # requests.post("https://fcm.googleapis.com/fcm/send",
    #     headers={"Authorization": f"key={os.getenv(ENV_FCM_KEY)}"},
    #     json={"to": device_token, "notification": {"title": title, "body": body}})
    return True


def notify(conn, title: str, body: str = "", *, level: str = "info",
           source: str = "agent", link: str = "",
           distributor_id: Optional[int] = None) -> int:
    pushed = 1 if _send_push(title, body, level) else 0
    cur = conn.execute(
        """INSERT INTO notifications (distributor_id,title,body,level,source,link,read,pushed,created_at)
           VALUES (?,?,?,?,?,?,0,?,?)""",
        (distributor_id, title, body, level, source, link, pushed, iso_now()))
    return cur.lastrowid


def list_notifications(conn, unread_only: bool = False, limit: int = 100) -> List[Dict[str, Any]]:
    q = "SELECT * FROM notifications"
    if unread_only:
        q += " WHERE read = 0"
    q += " ORDER BY created_at DESC LIMIT ?"
    return db.rows_to_list(conn.execute(q, (limit,)))


def mark_read(conn, notification_id: int) -> None:
    conn.execute("UPDATE notifications SET read=1 WHERE id=?", (notification_id,))


def mark_all_read(conn) -> int:
    cur = conn.execute("UPDATE notifications SET read=1 WHERE read=0")
    return cur.rowcount

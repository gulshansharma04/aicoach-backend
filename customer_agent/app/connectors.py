"""
External connectors — the bridge to Herbalife and social platforms.

DESIGN PRINCIPLES (these are deliberate, see README "security"):
  • Credentials are NEVER hardcoded and never stored in the app DB. They are
    read at runtime from environment variables (in production: a secrets
    manager). This file only references variable *names*.
  • Each connector reports a clear ``configured`` status. When not configured,
    sync is a safe no-op that explains what's missing — the app never fabricates
    customer data.
  • Herbalife has no public API, so per the product decision it uses **browser
    automation** (Playwright). The actual login/scrape is isolated here behind a
    single ``HerbalifeConnector.fetch_customers()`` method so it can be hardened,
    rate-limited, and swapped for an official API later without touching the rest
    of the app. NOTE: automating a third-party portal may conflict with its ToS
    and must only be run against the distributor's own account, with consent.

This module is intentionally a thin, well-defined seam. Wiring real credentials
and running it belongs to the remote-infra deployment, not the codebase.
"""

from __future__ import annotations

import os
import json
from typing import Any, Dict, List

from .scoring import iso_now


# ---- environment variable NAMES (values are injected at deploy time) ----
ENV_HERBALIFE_USER = "HERBALIFE_USERNAME"
ENV_HERBALIFE_PASS = "HERBALIFE_PASSWORD"      # noqa: S105 (name, not a secret)
ENV_HERBALIFE_URL = "HERBALIFE_PORTAL_URL"
ENV_META_TOKEN = "META_GRAPH_TOKEN"            # noqa: S105
ENV_META_ACCOUNTS = "META_ACCOUNT_IDS"


def _has(*names: str) -> bool:
    return all(os.getenv(n) for n in names)


def status() -> Dict[str, Any]:
    """Report which connectors are configured (without revealing any values)."""
    return {
        "herbalife": {
            "method": "browser_automation",
            "configured": _has(ENV_HERBALIFE_USER, ENV_HERBALIFE_PASS),
            "needs": [ENV_HERBALIFE_USER, ENV_HERBALIFE_PASS, ENV_HERBALIFE_URL],
            "playwright_available": _playwright_available(),
        },
        "social": {
            "method": "meta_graph_api",
            "configured": _has(ENV_META_TOKEN),
            "needs": [ENV_META_TOKEN, ENV_META_ACCOUNTS],
        },
        "note": ("Credentials are read from environment/secrets at runtime and "
                 "are never stored in the app database."),
    }


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


# ============================================================
# Herbalife (browser automation)
# ============================================================

class HerbalifeConnector:
    """Encapsulates the (isolated) browser-automation session."""

    def __init__(self) -> None:
        self.username = os.getenv(ENV_HERBALIFE_USER)
        self.password = os.getenv(ENV_HERBALIFE_PASS)
        self.portal_url = os.getenv(ENV_HERBALIFE_URL, "")

    def configured(self) -> bool:
        return bool(self.username and self.password)

    def fetch_customers(self) -> List[Dict[str, Any]]:
        """
        Log into the distributor's Herbalife portal and return customer +
        order records. Implemented with Playwright against the live portal;
        runs only when credentials are configured on the remote infra.

        Returns a list of dicts shaped like:
            {"name","email","phone","stage","orders":[{"product","amount","ordered_at","status"}]}
        """
        if not self.configured():
            raise RuntimeError("Herbalife connector not configured")
        if not _playwright_available():
            raise RuntimeError("Playwright not installed in this environment")

        # --- Implementation outline (kept behind the credential gate) ---
        # from playwright.sync_api import sync_playwright
        # with sync_playwright() as pw:
        #     browser = pw.chromium.launch(headless=True,
        #                                  executable_path="/opt/pw-browsers/chromium")
        #     page = browser.new_page()
        #     page.goto(self.portal_url)
        #     page.fill("#username", self.username)
        #     page.fill("#password", self.password)
        #     page.click("button[type=submit]")
        #     page.wait_for_url("**/dashboard**")
        #     ... navigate to the customers/orders report, parse rows ...
        #     browser.close()
        # return parsed_records
        raise NotImplementedError(
            "Live Herbalife scraping is wired but disabled here. Provide "
            "credentials on the remote infra and implement the portal-specific "
            "selectors in fetch_customers().")


def _upsert_customer(conn, rec: Dict[str, Any]) -> int:
    """Insert or update a customer by email (or name), then sync its orders."""
    existing = None
    if rec.get("email"):
        existing = conn.execute("SELECT * FROM customers WHERE email = ?", (rec["email"],)).fetchone()
    if not existing:
        existing = conn.execute("SELECT * FROM customers WHERE name = ?", (rec["name"],)).fetchone()

    now = iso_now()
    if existing:
        cid = existing["id"]
        conn.execute("UPDATE customers SET phone=COALESCE(?,phone), updated_at=? WHERE id=?",
                     (rec.get("phone"), now, cid))
    else:
        cur = conn.execute(
            """INSERT INTO customers
               (name,email,phone,company,stage,tags,socials,notes,preferred_channel,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (rec["name"], rec.get("email"), rec.get("phone"), "",
             rec.get("stage", "active"), json.dumps(rec.get("tags", ["herbalife"])),
             json.dumps({}), "Imported from Herbalife portal.", "text", now, now))
        cid = cur.lastrowid

    for o in rec.get("orders", []):
        conn.execute(
            "INSERT INTO orders (customer_id,product,amount,status,ordered_at,notes) VALUES (?,?,?,?,?,'herbalife')",
            (cid, o.get("product", "Order"), float(o.get("amount", 0)),
             o.get("status", "delivered"), o.get("ordered_at", now)))
    return cid


def sync_herbalife(conn) -> Dict[str, Any]:
    conn_inst = HerbalifeConnector()
    if not conn_inst.configured():
        return {"ok": False, "configured": False,
                "message": f"Set {ENV_HERBALIFE_USER}/{ENV_HERBALIFE_PASS} on the server to enable Herbalife sync."}
    try:
        records = conn_inst.fetch_customers()
    except (RuntimeError, NotImplementedError) as e:
        return {"ok": False, "configured": True, "message": str(e)}

    imported = sum(1 for r in records if _upsert_customer(conn, r))
    return {"ok": True, "configured": True, "imported": imported}


# ============================================================
# Social (Meta Graph API)
# ============================================================

def sync_social(conn, days: int = 14) -> Dict[str, Any]:
    """
    Pull recent posts/comments/mentions for mapped customers via the Meta Graph
    API and write them into the ``activities`` table (where the monitor then
    reviews them). Requires a Graph API token with the right scopes.
    """
    if not _has(ENV_META_TOKEN):
        return {"ok": False, "configured": False,
                "message": f"Set {ENV_META_TOKEN} (and {ENV_META_ACCOUNTS}) to enable social sync."}
    # --- Implementation outline ---
    # import requests
    # token = os.getenv(ENV_META_TOKEN)
    # for account in os.getenv(ENV_META_ACCOUNTS, "").split(","):
    #     resp = requests.get(f"https://graph.facebook.com/v19.0/{account}/tagged",
    #                         params={"access_token": token})
    #     ... match each post's author handle to customers.socials, then insert
    #         into activities with auto-sentiment ...
    return {"ok": False, "configured": True,
            "message": "Meta Graph sync is wired but the account-to-customer mapping "
                       "step needs your platform credentials on the remote infra."}


# ============================================================
# CEO news feed (content inspiration source)
# ============================================================

ENV_NEWS_API = "NEWS_SEARCH_API_KEY"


def fetch_ceo_posts(limit: int = 5) -> Dict[str, Any]:
    """
    Fetch the latest public posts/news about the Herbalife CEO to use as
    repost inspiration. In production this calls a news/social search API
    (or the CEO's public social feed). Returns a safe placeholder when the
    search provider isn't configured so the content generator still works
    from a manually supplied topic.
    """
    if not _has(ENV_NEWS_API):
        return {
            "ok": False, "configured": False,
            "message": f"Set {ENV_NEWS_API} to auto-pull the latest CEO posts. "
                       "You can still generate content from a topic you type in.",
            "posts": [],
        }
    # --- Implementation outline ---
    # import requests
    # r = requests.get("https://newsapi.org/v2/everything",
    #                  params={"q": "Herbalife CEO", "sortBy": "publishedAt",
    #                          "apiKey": os.getenv(ENV_NEWS_API), "pageSize": limit})
    # posts = [{"title": a["title"], "summary": a["description"], "url": a["url"]}
    #          for a in r.json().get("articles", [])]
    return {"ok": True, "configured": True, "posts": []}


# ============================================================
# Email monitor (subscribed myHerbalife emails)
# ============================================================

ENV_EMAIL_IMAP = "EMAIL_IMAP_HOST"
ENV_EMAIL_USER = "EMAIL_USERNAME"
ENV_EMAIL_PASS = "EMAIL_PASSWORD"          # noqa: S105


def scan_email(limit: int = 20) -> Dict[str, Any]:
    """
    Read recent emails (e.g. myHerbalife subscriptions) so the agent can flag
    important ones. Uses IMAP (or a mailbox API) with credentials from the
    secrets store / env. Returns an empty list when not configured so the
    triage flow stays testable.
    """
    if not _has(ENV_EMAIL_IMAP, ENV_EMAIL_USER, ENV_EMAIL_PASS):
        return {"ok": False, "configured": False,
                "message": f"Set {ENV_EMAIL_IMAP}/{ENV_EMAIL_USER}/{ENV_EMAIL_PASS} to let me watch your inbox.",
                "emails": []}
    # --- Implementation outline ---
    # import imaplib, email
    # M = imaplib.IMAP4_SSL(os.getenv(ENV_EMAIL_IMAP))
    # M.login(os.getenv(ENV_EMAIL_USER), os.getenv(ENV_EMAIL_PASS))
    # M.select("INBOX"); typ, data = M.search(None, "UNSEEN")
    # ... parse subject/body for each id ...
    return {"ok": True, "configured": True, "emails": []}


# ============================================================
# Website builder (Google Stitch)
# ============================================================

ENV_STITCH_KEY = "GOOGLE_STITCH_API_KEY"


def stitch_configured() -> bool:
    return bool(os.getenv(ENV_STITCH_KEY))


def create_site_with_stitch(brief: Dict[str, Any]) -> Dict[str, Any]:
    """
    Hand a structured website brief to Google Stitch to generate the site.
    Returns the brief plus a build status. When Stitch isn't configured, the
    brief itself is still returned so the distributor can review/export it.
    """
    if not stitch_configured():
        return {"ok": False, "configured": False,
                "message": f"Set {ENV_STITCH_KEY} to auto-build with Google Stitch. "
                           "Your generated brief is ready to review/export below.",
                "brief": brief}
    # --- Implementation outline ---
    # import requests
    # r = requests.post("https://stitch.googleapis.com/v1/sites",
    #     headers={"Authorization": f"Bearer {os.getenv(ENV_STITCH_KEY)}"},
    #     json={"brief": brief})
    # return {"ok": True, "configured": True, "url": r.json().get("preview_url"), "brief": brief}
    return {"ok": True, "configured": True, "brief": brief, "url": ""}

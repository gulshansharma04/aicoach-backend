"""
SQLite persistence layer.

Uses only the Python standard library (``sqlite3``) so the app runs with zero
extra infrastructure. The database file lives in ``customer_agent/data/crm.db``
by default and can be overridden with the ``CRM_DB_PATH`` env var.
"""

from __future__ import annotations

import os
import json
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "crm.db"
DB_PATH = Path(os.getenv("CRM_DB_PATH", str(DEFAULT_DB_PATH)))


SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    email           TEXT,
    phone           TEXT,
    company         TEXT,
    stage           TEXT NOT NULL DEFAULT 'prospect',   -- prospect|active|vip|inactive
    tags            TEXT NOT NULL DEFAULT '[]',          -- json array
    socials         TEXT NOT NULL DEFAULT '{}',          -- json: {platform: handle}
    notes           TEXT NOT NULL DEFAULT '',
    preferred_channel TEXT NOT NULL DEFAULT 'text',      -- call|text|email
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS progress (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER NOT NULL,
    title           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'in_progress', -- planned|in_progress|done|stalled
    note            TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER NOT NULL,
    product         TEXT NOT NULL,
    amount          REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'paid',        -- pending|paid|shipped|delivered|refunded
    ordered_at      TEXT NOT NULL,
    notes           TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS activities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER NOT NULL,
    platform        TEXT NOT NULL DEFAULT 'instagram',   -- instagram|facebook|tiktok|x|linkedin|whatsapp|other
    kind            TEXT NOT NULL DEFAULT 'comment',      -- post|comment|like|dm|mention|story|review
    content         TEXT NOT NULL DEFAULT '',
    sentiment       TEXT NOT NULL DEFAULT 'neutral',      -- positive|neutral|negative
    url             TEXT NOT NULL DEFAULT '',
    needs_response  INTEGER NOT NULL DEFAULT 0,           -- 0/1
    occurred_at     TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS interactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER NOT NULL,
    channel         TEXT NOT NULL DEFAULT 'call',         -- call|text|email|in_person|social
    summary         TEXT NOT NULL DEFAULT '',
    occurred_at     TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reminders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'follow_up',    -- call|text|follow_up|support|check_in
    reason          TEXT NOT NULL DEFAULT '',
    draft_message   TEXT NOT NULL DEFAULT '',
    priority        TEXT NOT NULL DEFAULT 'medium',       -- low|medium|high|urgent
    status          TEXT NOT NULL DEFAULT 'open',         -- open|done|dismissed
    due_date        TEXT,
    source          TEXT NOT NULL DEFAULT 'manual',       -- manual|agent
    created_at      TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

-- ============== Phase 2: autonomy, plans, agent actions ==============

CREATE TABLE IF NOT EXISTS comm_plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_steps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id         INTEGER NOT NULL,
    step_order      INTEGER NOT NULL DEFAULT 0,
    day_offset      INTEGER NOT NULL DEFAULT 0,           -- days after enrollment
    channel         TEXT NOT NULL DEFAULT 'text',         -- text|call|email|social
    goal            TEXT NOT NULL DEFAULT '',             -- what this touch should achieve
    risk            TEXT NOT NULL DEFAULT 'low',          -- low|sensitive
    FOREIGN KEY (plan_id) REFERENCES comm_plans(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS enrollments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER NOT NULL,
    plan_id         INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',        -- active|paused|completed
    current_step    INTEGER NOT NULL DEFAULT 0,            -- index into plan_steps order
    next_due        TEXT,
    started_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    FOREIGN KEY (plan_id) REFERENCES comm_plans(id) ON DELETE CASCADE
);

-- Every autonomous thing the agent does is logged here (transparency + counts).
CREATE TABLE IF NOT EXISTS agent_actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER NOT NULL,
    enrollment_id   INTEGER,
    activity_id     INTEGER,                               -- source social post, if any
    kind            TEXT NOT NULL DEFAULT 'reply',         -- review|reply|send|like|plan_step|monitor
    platform        TEXT NOT NULL DEFAULT '',
    channel         TEXT NOT NULL DEFAULT '',
    summary         TEXT NOT NULL DEFAULT '',
    draft           TEXT NOT NULL DEFAULT '',
    risk            TEXT NOT NULL DEFAULT 'low',           -- low|sensitive
    status          TEXT NOT NULL DEFAULT 'pending',       -- auto_done|pending|approved|rejected|sent
    created_at      TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_plan_steps_plan      ON plan_steps(plan_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_customer ON enrollments(customer_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_status   ON enrollments(status);
CREATE INDEX IF NOT EXISTS idx_actions_customer     ON agent_actions(customer_id);
CREATE INDEX IF NOT EXISTS idx_actions_status       ON agent_actions(status);
CREATE INDEX IF NOT EXISTS idx_actions_created      ON agent_actions(created_at);

CREATE INDEX IF NOT EXISTS idx_progress_customer    ON progress(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_customer      ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_activities_customer  ON activities(customer_id);
CREATE INDEX IF NOT EXISTS idx_interactions_customer ON interactions(customer_id);
CREATE INDEX IF NOT EXISTS idx_reminders_customer   ON reminders(customer_id);
CREATE INDEX IF NOT EXISTS idx_reminders_status     ON reminders(status);
"""

# Columns stored as JSON strings and transparently (de)serialized.
_JSON_COLUMNS = {"tags", "socials"}


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    out: Dict[str, Any] = dict(row)
    for col in _JSON_COLUMNS:
        if col in out and isinstance(out[col], str):
            try:
                out[col] = json.loads(out[col])
            except (json.JSONDecodeError, TypeError):
                out[col] = [] if col == "tags" else {}
    return out


def rows_to_list(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [row_to_dict(r) for r in rows]  # type: ignore[misc]

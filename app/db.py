import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    -- Bumped on password change so existing sessions are signed out.
    session_version INTEGER NOT NULL DEFAULT 0
);

-- Single-use links: 'invite' creates an account, 'reset' sets a new password.
-- Only a hash of the token is stored.
CREATE TABLE IF NOT EXISTS invites (
    id          INTEGER PRIMARY KEY,
    token_hash  TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL CHECK (kind IN ('invite', 'reset')),
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
    is_admin    INTEGER NOT NULL DEFAULT 0,
    note        TEXT NOT NULL DEFAULT '',
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT NOT NULL,
    used_at     TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Only what the calendar needs. Phones/addresses from the export are never stored.
CREATE TABLE IF NOT EXISTS birthdays (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,      -- as exported: "Last, First Middle"
    month      INTEGER NOT NULL,
    day        INTEGER NOT NULL,
    UNIQUE (name, month, day)
);

CREATE TABLE IF NOT EXISTS uploads (
    id          INTEGER PRIMARY KEY,
    report      TEXT NOT NULL,
    uploaded_by TEXT NOT NULL,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now')),
    summary     TEXT NOT NULL
);
"""


def init() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        _migrate(conn)
        conn.executescript(SCHEMA)


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring databases created by older versions up to the current schema."""
    users_cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if users_cols and "session_version" not in users_cols:
        conn.execute("ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0")


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )

import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    is_leader     INTEGER NOT NULL DEFAULT 0,   -- can use Ministering (admins always can)
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
    is_leader   INTEGER NOT NULL DEFAULT 0,
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
    missing_since TEXT,                -- set when a later upload didn't include them
    kept       INTEGER NOT NULL DEFAULT 0,  -- someone chose to keep them anyway
    UNIQUE (name, month, day)
);

-- Ward roster from the LCR Member List. People are never deleted; move-outs become inactive.
CREATE TABLE IF NOT EXISTS people (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,           -- "Last, First Middle"
    gender      TEXT CHECK (gender IN ('M', 'F')),
    birth_year  INTEGER,
    birth_month INTEGER,
    birth_day   INTEGER,
    active      INTEGER NOT NULL DEFAULT 1,
    first_seen  TEXT NOT NULL DEFAULT (datetime('now')),
    left_at     TEXT
);

CREATE TABLE IF NOT EXISTS person_tags (
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    tag       TEXT NOT NULL,
    PRIMARY KEY (person_id, tag)
);

-- A ministering layout: an imported LCR snapshot (read-only) or an editable draft.
CREATE TABLE IF NOT EXISTS layouts (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    org         TEXT NOT NULL DEFAULT 'eq' CHECK (org IN ('eq', 'rs')),  -- Elders Quorum / Relief Society
    kind        TEXT NOT NULL CHECK (kind IN ('imported', 'draft')),
    status      TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'proposed', 'approved')),
    is_current  INTEGER NOT NULL DEFAULT 0,  -- the latest import, i.e. what's in LCR now
    version     INTEGER NOT NULL DEFAULT 1,  -- bumped on every save, for edit conflicts
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS districts (
    id         INTEGER PRIMARY KEY,
    layout_id  INTEGER NOT NULL REFERENCES layouts(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    supervisor TEXT NOT NULL DEFAULT '',
    position   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS companionships (
    id          INTEGER PRIMARY KEY,
    district_id INTEGER NOT NULL REFERENCES districts(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS companionship_members (
    companionship_id INTEGER NOT NULL REFERENCES companionships(id) ON DELETE CASCADE,
    person_id        INTEGER NOT NULL REFERENCES people(id),
    role             TEXT NOT NULL CHECK (role IN ('minister', 'assigned')),
    position         INTEGER NOT NULL,
    PRIMARY KEY (companionship_id, person_id, role)
);

-- Parsed ministering report waiting for unmatched names to be resolved.
CREATE TABLE IF NOT EXISTS pending_imports (
    id         INTEGER PRIMARY KEY,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    payload    TEXT NOT NULL
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
    if users_cols and "is_leader" not in users_cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_leader INTEGER NOT NULL DEFAULT 0")
    bday_cols = {r["name"] for r in conn.execute("PRAGMA table_info(birthdays)")}
    if bday_cols and "missing_since" not in bday_cols:
        conn.execute("ALTER TABLE birthdays ADD COLUMN missing_since TEXT")
        conn.execute("ALTER TABLE birthdays ADD COLUMN kept INTEGER NOT NULL DEFAULT 0")
    layout_cols = {r["name"] for r in conn.execute("PRAGMA table_info(layouts)")}
    if layout_cols and "org" not in layout_cols:
        # Layouts made before Relief Society support were all elders quorum.
        conn.execute("ALTER TABLE layouts ADD COLUMN org TEXT NOT NULL DEFAULT 'eq'")
    invite_cols = {r["name"] for r in conn.execute("PRAGMA table_info(invites)")}
    if invite_cols and "is_leader" not in invite_cols:
        conn.execute("ALTER TABLE invites ADD COLUMN is_leader INTEGER NOT NULL DEFAULT 0")


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

"""Storage: one central database for accounts and wards, plus one database file per ward.

Keeping each ward's member data in its own file means a query can only ever see the ward whose
file it opened: there's no ward_id filter to forget.
"""
import secrets
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import config

CENTRAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS wards (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    feed_token  TEXT NOT NULL UNIQUE,   -- secret part of the ward's birthday calendar link
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    ward_id       INTEGER REFERENCES wards(id),  -- home ward; admins can switch to any ward
    is_admin      INTEGER NOT NULL DEFAULT 0,    -- site admin: every ward
    is_ward_admin INTEGER NOT NULL DEFAULT 0,    -- manages accounts in their own ward
    is_leader     INTEGER NOT NULL DEFAULT 0,    -- can use Ministering in their ward
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    -- Bumped on password change so existing sessions are signed out.
    session_version INTEGER NOT NULL DEFAULT 0
);

-- Single-use links: 'invite' creates an account, 'reset' sets a new password.
-- Only a hash of the token is stored.
CREATE TABLE IF NOT EXISTS invites (
    id            INTEGER PRIMARY KEY,
    token_hash    TEXT NOT NULL UNIQUE,
    kind          TEXT NOT NULL CHECK (kind IN ('invite', 'reset')),
    user_id       INTEGER REFERENCES users(id) ON DELETE CASCADE,
    ward_id       INTEGER REFERENCES wards(id),
    is_admin      INTEGER NOT NULL DEFAULT 0,
    is_ward_admin INTEGER NOT NULL DEFAULT 0,
    is_leader     INTEGER NOT NULL DEFAULT 0,
    note          TEXT NOT NULL DEFAULT '',
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at    TEXT NOT NULL,
    used_at       TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

WARD_SCHEMA = """
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
    approved_at TEXT,                         -- when it was last set to Approved
    verified_at TEXT,                         -- when a later LCR import was found to match it
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

# Ward tables in an order that respects foreign keys (parents first).
WARD_TABLES = ["people", "person_tags", "layouts", "districts", "companionships", "companionship_members",
               "pending_imports", "birthdays", "uploads"]


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def _session(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def connect():
    """The central database: wards, users, invites."""
    with _session(_open(config.DB_PATH)) as conn:
        yield conn


def ward_path(ward_id: int) -> Path:
    return config.DATA_DIR / "wards" / f"ward-{int(ward_id)}.db"


_ready: set[str] = set()


@contextmanager
def ward_connect(ward_id: int):
    """One ward's own database: roster, birthdays, ministering."""
    path = ward_path(ward_id)
    if str(path) not in _ready:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _session(_open(path)) as conn:
            _migrate_columns(conn)
            conn.executescript(WARD_SCHEMA)
        _ready.add(str(path))
    with _session(_open(path)) as conn:
        yield conn


def _columns(conn, table: str, schema: str = "main") -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA {schema}.table_info({table})")]


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """Add columns that older versions didn't have (to whichever tables exist in this file)."""
    def add(table, column, ddl):
        cols = _columns(conn, table)
        if cols and column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    add("users", "session_version", "INTEGER NOT NULL DEFAULT 0")
    add("users", "is_leader", "INTEGER NOT NULL DEFAULT 0")
    add("users", "is_ward_admin", "INTEGER NOT NULL DEFAULT 0")
    add("users", "ward_id", "INTEGER REFERENCES wards(id)")
    add("invites", "is_leader", "INTEGER NOT NULL DEFAULT 0")
    add("invites", "is_ward_admin", "INTEGER NOT NULL DEFAULT 0")
    add("invites", "ward_id", "INTEGER REFERENCES wards(id)")
    if _columns(conn, "birthdays") and "missing_since" not in _columns(conn, "birthdays"):
        conn.execute("ALTER TABLE birthdays ADD COLUMN missing_since TEXT")
        conn.execute("ALTER TABLE birthdays ADD COLUMN kept INTEGER NOT NULL DEFAULT 0")
    # Layouts made before Relief Society support were all elders quorum.
    add("layouts", "org", "TEXT NOT NULL DEFAULT 'eq'")
    if _columns(conn, "layouts") and "approved_at" not in _columns(conn, "layouts"):
        conn.execute("ALTER TABLE layouts ADD COLUMN approved_at TEXT")
        conn.execute("ALTER TABLE layouts ADD COLUMN verified_at TEXT")
        # Layouts approved before the LCR check existed count as done, so the upgrade doesn't nag.
        conn.execute("UPDATE layouts SET approved_at = updated_at, verified_at = updated_at "
                     "WHERE kind = 'draft' AND status = 'approved'")


def init() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    legacy = False
    if config.DB_PATH.exists():
        probe = _open(config.DB_PATH)
        marks = ",".join("?" * len(WARD_TABLES))
        legacy = bool(probe.execute(
            f"SELECT 1 FROM sqlite_master WHERE type = 'table' AND name IN ({marks})", WARD_TABLES).fetchone())
        probe.close()
    if legacy:
        # Before moving anything, keep a copy of the single-ward database.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(config.DB_PATH, config.DB_PATH.with_name(f"portal.pre-wards-{stamp}.db.bak"))

    with connect() as conn:
        _migrate_columns(conn)
        conn.executescript(CENTRAL_SCHEMA)
        if not conn.execute("SELECT 1 FROM wards LIMIT 1").fetchone():
            # First run (or upgrade): the existing calendar link carries over to the first ward.
            token = get_setting(conn, "feed_token") or secrets.token_urlsafe(32)
            conn.execute("INSERT INTO wards (name, feed_token) VALUES (?, ?)", (config.WARD_NAME, token))
            conn.execute("DELETE FROM settings WHERE key = 'feed_token'")
        first = conn.execute("SELECT MIN(id) FROM wards").fetchone()[0]
        conn.execute("UPDATE users SET ward_id = ? WHERE ward_id IS NULL", (first,))
        conn.execute("UPDATE invites SET ward_id = ? WHERE ward_id IS NULL AND kind = 'invite'", (first,))

    if legacy:
        _split_legacy(first)

    with connect() as conn:
        ward_ids = [r[0] for r in conn.execute("SELECT id FROM wards")]
    for wid in ward_ids:
        with ward_connect(wid):
            pass


def _split_legacy(ward_id: int) -> None:
    """Move a single-ward database's member data into that ward's own file, in one transaction."""
    with ward_connect(ward_id):
        pass  # create the ward file with the current schema
    conn = _open(config.DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")  # rows are copied as-is, ids included
    conn.execute("ATTACH DATABASE ? AS w", (str(ward_path(ward_id)),))
    try:
        for table in WARD_TABLES:
            cols = _columns(conn, table)
            if not cols:
                continue
            if conn.execute(f"SELECT 1 FROM w.{table} LIMIT 1").fetchone():
                raise RuntimeError(f"Ward {ward_id} already has {table} data; not overwriting it.")
            names = ", ".join(cols)
            conn.execute(f"INSERT INTO w.{table} ({names}) SELECT {names} FROM main.{table}")
        for table in reversed(WARD_TABLES):
            conn.execute(f"DROP TABLE IF EXISTS main.{table}")
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

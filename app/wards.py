"""Wards: each has its own name, calendar link and database file."""
import secrets
import sqlite3

from . import db


def list_wards(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT w.*, (SELECT COUNT(*) FROM users u WHERE u.ward_id = w.id) AS users "
        "FROM wards w ORDER BY w.name COLLATE NOCASE")]


def get(conn: sqlite3.Connection, ward_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM wards WHERE id = ?", (ward_id,)).fetchone()
    return dict(row) if row else None


def by_feed_token(conn: sqlite3.Connection, token: str) -> dict | None:
    row = conn.execute("SELECT * FROM wards WHERE feed_token = ?", (token,)).fetchone()
    return dict(row) if row else None


def _clean_name(name: str) -> str:
    name = " ".join(name.split())[:80]
    if not name:
        raise ValueError("Give the ward a name.")
    return name


def create(conn: sqlite3.Connection, name: str) -> int:
    ward_id = conn.execute("INSERT INTO wards (name, feed_token) VALUES (?, ?)",
                           (_clean_name(name), secrets.token_urlsafe(32))).lastrowid
    conn.commit()  # the ward's own file is created next, outside this transaction
    with db.ward_connect(ward_id):
        pass
    return ward_id


def rename(conn: sqlite3.Connection, ward_id: int, name: str) -> None:
    conn.execute("UPDATE wards SET name = ? WHERE id = ?", (_clean_name(name), ward_id))


def new_feed_token(conn: sqlite3.Connection, ward_id: int) -> None:
    conn.execute("UPDATE wards SET feed_token = ? WHERE id = ?", (secrets.token_urlsafe(32), ward_id))


def people_count(ward_id: int) -> int:
    with db.ward_connect(ward_id) as wconn:
        return wconn.execute("SELECT COUNT(*) FROM people WHERE active = 1").fetchone()[0]


def initials(name: str) -> str:
    """Logo mark: initials of the first two words ("Kays Creek YSA Ward" -> "KC")."""
    return "".join(w[0] for w in name.split()[:2]).upper() or "W"

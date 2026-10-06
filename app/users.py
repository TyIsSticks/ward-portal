"""User management and single-use invite / password-reset links."""
import hashlib
import secrets
import sqlite3

from . import auth

LINK_TTL_DAYS = 7


class LinkError(ValueError):
    pass


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def list_users(conn: sqlite3.Connection, ward_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, username, ward_id, is_admin, is_ward_admin, is_leader, created_at FROM users "
        "WHERE ward_id = ? ORDER BY username COLLATE NOCASE", (ward_id,))
    return [dict(r) | {"role": auth.role_of(r)} for r in rows]


def get_user(conn: sqlite3.Connection, user_id: int) -> dict | None:
    row = conn.execute("SELECT id, username, ward_id, is_admin, is_ward_admin, is_leader FROM users WHERE id = ?",
                       (user_id,)).fetchone()
    return dict(row) | {"role": auth.role_of(row)} if row else None


def set_role(conn: sqlite3.Connection, user_id: int, role: str) -> None:
    is_admin, is_ward_admin, is_leader = auth.role_flags(role)
    conn.execute("UPDATE users SET is_admin = ?, is_ward_admin = ?, is_leader = ? WHERE id = ?",
                 (is_admin, is_ward_admin, is_leader, user_id))


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


# --- Links ------------------------------------------------------------------

def _create_link(conn, kind: str, created_by: str, *, user_id=None, ward_id=None, role="member", note="") -> str:
    is_admin, is_ward_admin, is_leader = auth.role_flags(role)
    token = secrets.token_urlsafe(24)
    if kind == "reset":
        # Only the newest reset link for a user should work.
        conn.execute("DELETE FROM invites WHERE kind = 'reset' AND user_id = ? AND used_at IS NULL", (user_id,))
    conn.execute(
        "INSERT INTO invites (token_hash, kind, user_id, ward_id, is_admin, is_ward_admin, is_leader, note, "
        f"created_by, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+{LINK_TTL_DAYS} days'))",
        (_hash(token), kind, user_id, ward_id, is_admin, is_ward_admin, is_leader, note.strip()[:100], created_by),
    )
    return token


def create_invite(conn, created_by: str, ward_id: int, role: str = "member", note: str = "") -> str:
    return _create_link(conn, "invite", created_by, ward_id=ward_id, role=role, note=note)


def create_reset(conn, created_by: str, user_id: int) -> str:
    return _create_link(conn, "reset", created_by, user_id=user_id)


def pending_invites(conn, ward_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, is_admin, is_ward_admin, is_leader, note, created_by, created_at, expires_at FROM invites "
        "WHERE kind = 'invite' AND ward_id = ? AND used_at IS NULL AND expires_at > datetime('now') "
        "ORDER BY id DESC", (ward_id,))
    return [dict(r) | {"role": auth.role_of(r)} for r in rows]


def revoke(conn, invite_id: int, ward_id: int) -> None:
    conn.execute("DELETE FROM invites WHERE id = ? AND ward_id = ? AND used_at IS NULL", (invite_id, ward_id))


def lookup(conn, token: str) -> dict | None:
    """The link's details if it's unused and unexpired, else None."""
    row = conn.execute(
        "SELECT i.*, u.username, w.name AS ward_name FROM invites i LEFT JOIN users u ON u.id = i.user_id "
        "LEFT JOIN wards w ON w.id = COALESCE(i.ward_id, u.ward_id) "
        "WHERE i.token_hash = ? AND i.used_at IS NULL AND i.expires_at > datetime('now')",
        (_hash(token),)).fetchone()
    return dict(row) if row else None


def _consume(conn, link_id: int) -> None:
    # The used_at guard makes a double-submit lose the race instead of creating two accounts.
    cur = conn.execute(
        "UPDATE invites SET used_at = datetime('now') WHERE id = ? AND used_at IS NULL", (link_id,))
    if cur.rowcount != 1:
        raise LinkError("This link has already been used.")


def redeem(conn, token: str, password: str, username: str | None = None) -> int:
    """Use an invite (creates the account) or reset link (sets the password). Returns the user id."""
    link = lookup(conn, token)
    if link is None:
        raise LinkError("This link is invalid, expired or already used.")
    _consume(conn, link["id"])
    if link["kind"] == "invite":
        try:
            return auth.insert_user(conn, username or "", password, role=auth.role_of(link), ward_id=link["ward_id"])
        except sqlite3.IntegrityError:
            raise LinkError("That username is taken. Pick another.") from None
    auth.update_password(conn, link["user_id"], password)
    return link["user_id"]

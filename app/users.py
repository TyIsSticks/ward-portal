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


def list_users(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, username, is_admin, created_at FROM users ORDER BY username COLLATE NOCASE")
    return [dict(r) for r in rows]


def set_admin(conn: sqlite3.Connection, user_id: int, is_admin: bool) -> None:
    conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(is_admin), user_id))


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


# --- Links ------------------------------------------------------------------

def _create_link(conn, kind: str, created_by: str, *, user_id=None, is_admin=False, note="") -> str:
    token = secrets.token_urlsafe(24)
    if kind == "reset":
        # Only the newest reset link for a user should work.
        conn.execute("DELETE FROM invites WHERE kind = 'reset' AND user_id = ? AND used_at IS NULL", (user_id,))
    conn.execute(
        "INSERT INTO invites (token_hash, kind, user_id, is_admin, note, created_by, expires_at) "
        f"VALUES (?, ?, ?, ?, ?, ?, datetime('now', '+{LINK_TTL_DAYS} days'))",
        (_hash(token), kind, user_id, int(is_admin), note.strip()[:100], created_by),
    )
    return token


def create_invite(conn, created_by: str, is_admin: bool = False, note: str = "") -> str:
    return _create_link(conn, "invite", created_by, is_admin=is_admin, note=note)


def create_reset(conn, created_by: str, user_id: int) -> str:
    return _create_link(conn, "reset", created_by, user_id=user_id)


def pending_invites(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id, is_admin, note, created_by, created_at, expires_at FROM invites "
        "WHERE kind = 'invite' AND used_at IS NULL AND expires_at > datetime('now') ORDER BY id DESC")
    return [dict(r) for r in rows]


def revoke(conn, invite_id: int) -> None:
    conn.execute("DELETE FROM invites WHERE id = ? AND used_at IS NULL", (invite_id,))


def lookup(conn, token: str) -> dict | None:
    """The link's details if it's unused and unexpired, else None."""
    row = conn.execute(
        "SELECT i.*, u.username FROM invites i LEFT JOIN users u ON u.id = i.user_id "
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
            return auth.insert_user(conn, username or "", password, is_admin=bool(link["is_admin"]))
        except sqlite3.IntegrityError:
            raise LinkError("That username is taken. Pick another.") from None
    auth.update_password(conn, link["user_id"], password)
    return link["user_id"]

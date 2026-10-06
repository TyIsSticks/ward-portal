import hashlib
import hmac
import re
import secrets
import time

from fastapi import HTTPException, Request

from . import db

_SCRYPT = dict(n=2**14, r=8, p=1, dklen=64)

# Per-username lockout after repeated failed logins (in memory; resets on restart).
_MAX_FAILURES = 5
_LOCKOUT_SECONDS = 15 * 60
_failures: dict[str, tuple[int, float]] = {}

MIN_PASSWORD = 10
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")


def validate_username(username: str) -> str:
    username = username.strip()
    if not _USERNAME_RE.match(username):
        raise ValueError("Usernames are 3–32 characters: letters, numbers, dots, dashes or underscores.")
    return username


def validate_password(password: str, confirm: str | None = None) -> str:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Passwords need at least {MIN_PASSWORD} characters.")
    if confirm is not None and password != confirm:
        raise ValueError("The passwords didn't match.")
    return password


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(digest.hex(), digest_hex)


# Everything but "admin" is scoped to the user's own ward. Admins are site-wide.
ROLES = ("member", "leader", "ward_admin", "admin")
ROLE_LABELS = {"member": "Member", "leader": "Leader", "ward_admin": "Ward admin", "admin": "Admin (all wards)"}


def role_flags(role: str) -> tuple[int, int, int]:
    """(is_admin, is_ward_admin, is_leader) for a role name."""
    if role not in ROLES:
        raise ValueError(f"Unknown role {role!r}")
    return int(role == "admin"), int(role == "ward_admin"), int(role == "leader")


def role_of(row) -> str:
    if row["is_admin"]:
        return "admin"
    if row["is_ward_admin"]:
        return "ward_admin"
    return "leader" if row["is_leader"] else "member"


def default_ward_id(conn) -> int:
    return conn.execute("SELECT MIN(id) FROM wards").fetchone()[0]


def insert_user(conn, username: str, password: str, role: str = "member", ward_id: int | None = None) -> int:
    is_admin, is_ward_admin, is_leader = role_flags(role)
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, ward_id, is_admin, is_ward_admin, is_leader) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (validate_username(username), hash_password(password), ward_id or default_ward_id(conn),
         is_admin, is_ward_admin, is_leader),
    )
    return cur.lastrowid


def create_user(username: str, password: str, is_admin: bool = False, role: str | None = None,
                ward_id: int | None = None) -> int:
    with db.connect() as conn:
        return insert_user(conn, username, password, role or ("admin" if is_admin else "member"), ward_id)


def update_password(conn, user_id: int, password: str) -> None:
    """Set a new password and sign the user out of every existing session."""
    conn.execute(
        "UPDATE users SET password_hash = ?, session_version = session_version + 1 WHERE id = ?",
        (hash_password(password), user_id),
    )


def set_password(username: str, password: str) -> bool:
    with db.connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username.strip(),)).fetchone()
        if not row:
            return False
        update_password(conn, row["id"], password)
        return True


def authenticate(username: str, password: str) -> dict | None:
    key = username.strip().lower()
    count, since = _failures.get(key, (0, 0.0))
    if count >= _MAX_FAILURES and time.time() - since < _LOCKOUT_SECONDS:
        return None

    with db.connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()

    if row and verify_password(password, row["password_hash"]):
        _failures.pop(key, None)
        return dict(row)

    _failures[key] = (count + 1, time.time())
    return None


def log_in(request: Request, user_id: int) -> None:
    with db.connect() as conn:
        row = conn.execute("SELECT session_version FROM users WHERE id = ?", (user_id,)).fetchone()
    request.session.clear()
    request.session["user_id"] = user_id
    request.session["sv"] = row["session_version"]


def current_user(request: Request) -> dict | None:
    """The signed-in user, with the ward they're working in as user["ward"].

    Everyone works in their own ward, except admins, who can switch (kept in the session).
    """
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, username, ward_id, is_admin, is_ward_admin, is_leader, session_version "
            "FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or row["session_version"] != request.session.get("sv"):
            return None
        ward_id = row["ward_id"]
        if row["is_admin"] and request.session.get("ward_id"):
            ward_id = request.session["ward_id"]
        ward = conn.execute("SELECT * FROM wards WHERE id = ?", (ward_id,)).fetchone()             or conn.execute("SELECT * FROM wards ORDER BY id LIMIT 1").fetchone()
    user = dict(row)
    user["role"] = role_of(row)
    user["role_label"] = ROLE_LABELS[user["role"]]
    user["leads"] = user["role"] in ("leader", "ward_admin", "admin")
    user["manages"] = user["role"] in ("ward_admin", "admin")  # can manage accounts in the ward
    user["ward"] = dict(ward)
    return user


class LoginRequired(Exception):
    pass


def require_user(request: Request) -> dict:
    user = current_user(request)
    if user is None:
        raise LoginRequired()
    return user


def require_leader(request: Request) -> dict:
    user = require_user(request)
    if not user["leads"]:
        raise HTTPException(status_code=403, detail="Leaders only")
    return user


def require_manager(request: Request) -> dict:
    """Ward admins (their own ward) and admins (any ward)."""
    user = require_user(request)
    if not user["manages"]:
        raise HTTPException(status_code=403, detail="Ward admins only")
    return user


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admins only")
    return user

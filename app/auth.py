import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request

from . import db

_SCRYPT = dict(n=2**14, r=8, p=1, dklen=64)

# Per-username lockout after repeated failed logins (in memory; resets on restart).
_MAX_FAILURES = 5
_LOCKOUT_SECONDS = 15 * 60
_failures: dict[str, tuple[int, float]] = {}


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


def create_user(username: str, password: str, is_admin: bool = False) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
            (username.strip(), hash_password(password), int(is_admin)),
        )


def set_password(username: str, password: str) -> bool:
    with db.connect() as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (hash_password(password), username.strip()),
        )
        return cur.rowcount > 0


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


def current_user(request: Request) -> dict | None:
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    with db.connect() as conn:
        row = conn.execute("SELECT id, username, is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


class LoginRequired(Exception):
    pass


def require_user(request: Request) -> dict:
    user = current_user(request)
    if user is None:
        raise LoginRequired()
    return user


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admins only")
    return user

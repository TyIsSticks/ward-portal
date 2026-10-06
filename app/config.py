import os
import secrets
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "portal.db"

# Name for the first ward (existing installs keep theirs). More wards are added in the app.
WARD_NAME = os.environ.get("WARD_NAME", "My Ward")
# Shown before anyone signs in, when there's no ward yet.
SITE_NAME = os.environ.get("SITE_NAME", "Ward Portal")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def secret_key() -> str:
    """Session signing key: SECRET_KEY env var, else generated once and kept in the data dir."""
    if key := os.environ.get("SECRET_KEY"):
        return key
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "secret_key"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(48))
    return path.read_text().strip()

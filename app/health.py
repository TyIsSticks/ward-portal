"""Status page: is data fresh, are the bots posting, are backups running. Quiet failures made visible.

Each check is {label, state, text, href?} where state is "ok", "warn", "bad" or "info".
"""
import json
import shutil
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import backup, config, groupme, ministering as m

STALE_DAYS = 21          # same threshold the dashboard uses for the Member List
LOW_DISK_BYTES = 500 * 1024 * 1024


def _age_days(stamp: str) -> int:
    """Whole days since a SQLite UTC timestamp ('YYYY-MM-DD HH:MM:SS')."""
    return (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S")).days


def _ago(days: int) -> str:
    return "today" if days <= 0 else "yesterday" if days == 1 else f"{days} days ago"


def _upload_check(conn: sqlite3.Connection, label: str, reports: tuple[str, ...], href: str, none: str) -> dict:
    marks = ",".join("?" * len(reports))
    row = conn.execute(f"SELECT uploaded_at, uploaded_by FROM uploads WHERE report IN ({marks}) "
                       "ORDER BY id DESC LIMIT 1", reports).fetchone()
    if row is None:
        return {"label": label, "state": "bad", "text": none, "href": href}
    days = _age_days(row["uploaded_at"])
    return {"label": label, "state": "warn" if days > STALE_DAYS else "ok", "href": href,
            "text": f"Last uploaded {_ago(days)} by {row['uploaded_by']}"
                    + (f" — over {STALE_DAYS // 7} weeks old" if days > STALE_DAYS else "")}


def ward_checks(conn: sqlite3.Connection, leads: bool = True) -> list[dict]:
    """Data freshness and bot health for the ward this connection is open on."""
    checks = [_upload_check(conn, "Member List", ("directory",), "/upload", "Never uploaded")]
    if leads:
        for org, name in m.ORGS.items():
            cur = m.current_import(conn, org)
            if cur is None:
                checks.append({"label": f"{name} assignments", "state": "info", "href": f"/ministering/{org}#import",
                               "text": "Not imported from LCR yet"})
            else:
                days = _age_days(cur["created_at"])
                checks.append({"label": f"{name} assignments", "state": "ok", "href": f"/ministering/{org}",
                               "text": f"Imported from LCR {_ago(days)}"})
    for key, name in groupme.BOTS.items():
        s = groupme.status(conn, key)
        if not s["configured"]:
            checks.append({"label": name, "state": "info", "text": "Not set up"})
        elif not config.GROUPME_ENABLED:
            checks.append({"label": name, "state": "warn", "text": "Set up, but posting is switched off (GROUPME_ENABLED=false)"})
        elif s["last"] is None:
            checks.append({"label": name, "state": "info", "text": "Set up; hasn’t posted yet"})
        elif s["last"]["ok"]:
            checks.append({"label": name, "state": "ok", "text": f"Last posted {s['last']['at']}"})
        else:
            checks.append({"label": name, "state": "bad",
                           "text": f"Last post failed ({s['last']['at']}): {s['last']['error']}"})
    return checks


def server_checks() -> list[dict]:
    """Backups and disk space (the whole portal, not one ward)."""
    out = []
    autos = backup.listing("auto")
    err = backup.last_error()
    if not config.BACKUP_ENABLED:
        out.append({"label": "Nightly backup", "state": "warn", "text": "Switched off (BACKUP_ENABLED=false)"})
    elif err:
        out.append({"label": "Nightly backup", "state": "bad", "text": f"Failing: {err}"})
    elif not autos:
        out.append({"label": "Nightly backup", "state": "info", "text": "No backup yet; the first runs after 3 am"})
    else:
        days = (groupme.now().replace(tzinfo=None) - autos[0]["at"]).days
        out.append({"label": "Nightly backup", "state": "warn" if days >= 2 else "ok",
                    "text": f"Last backup {_ago(days)} ({autos[0]['at']:%b} {autos[0]['at'].day})"
                            + (" — it should run every night" if days >= 2 else "")})
    free = shutil.disk_usage(config.DATA_DIR).free
    out.append({"label": "Disk space", "state": "warn" if free < LOW_DISK_BYTES else "ok",
                "text": f"{size(free)} free"})
    return out


def size(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} bytes" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def problems(conn: sqlite3.Connection, is_admin: bool) -> list[dict]:
    """Dashboard to-do items for things that are quietly broken (GroupMe posts failing, backups failing)."""
    todo = [{"text": f"{c['label']}: the last post failed", "cta": "Status", "href": "/admin/status"}
            for c in ward_checks(conn, leads=False) if c["state"] == "bad" and c["label"] in groupme.BOTS.values()]
    if is_admin and config.BACKUP_ENABLED:
        bad = [c for c in server_checks() if c["state"] == "bad" or (c["label"] == "Nightly backup" and c["state"] == "warn")]
        todo += [{"text": f"{c['label']}: {c['text']}", "cta": "Status", "href": "/admin/status"} for c in bad]
    return todo


def check_public_link(url: str) -> tuple[bool, str]:
    """Ask the public address for /healthz from this server. A failure here can also mean the server
    can't reach its own public address (some networks don't loop back), so it's a hint, not proof."""
    target = f"{url.rstrip('/')}/healthz"
    try:
        with urllib.request.urlopen(urllib.request.Request(target, headers={"User-Agent": "ward-portal"}), timeout=8) as r:
            body = json.loads(r.read(1000) or b"null")
        return (bool(body and body.get("ok")), "Reachable." if body and body.get("ok") else "Answered, but not from the portal.")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"Couldn’t reach {target}: {getattr(exc, 'reason', exc)}"

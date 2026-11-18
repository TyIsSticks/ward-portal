"""Backups: a nightly snapshot of every database, kept in data/backups, plus download and restore.

A backup is a zip of portal.db and each ward's database, taken with SQLite's online backup so it is
consistent even while the app is running. The session key is left out (it's not needed; signing in
again is enough after a restore).

Backups sit on the same disk as the data, so they protect against a bad upgrade or a mistake, not a
dead disk. Download one now and then (Status page) or copy the data folder somewhere else.
"""
import asyncio
import logging
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import config, db, groupme

log = logging.getLogger(__name__)

BACKUP_HOUR = 3                      # the nightly backup runs from 3 am, local time
CHECK_EVERY = 5 * 60
START_DELAY = 60
KEEP = {"auto": 14, "manual": 10, "pre-restore": 3}
NAME_RE = re.compile(r"^(auto|manual|pre-restore)-(\d{8}-\d{6})(-\d+)?\.zip$")
ALLOWED = re.compile(r"^(portal\.db|wards/ward-\d+\.db)$")


class BackupError(Exception):
    pass


def folder() -> Path:
    return config.DATA_DIR / "backups"


def _sources() -> list[tuple[str, Path]]:
    """(name inside the zip, file) for the central database and each ward's."""
    found = [("portal.db", config.DB_PATH)] if config.DB_PATH.exists() else []
    wards = config.DATA_DIR / "wards"
    if wards.is_dir():
        found += [(f"wards/{p.name}", p) for p in sorted(wards.glob("ward-*.db"))]
    return found


def _snapshot(src: Path, dest: Path) -> None:
    source, target = sqlite3.connect(src), sqlite3.connect(dest)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def create(kind: str = "manual") -> Path:
    """Write a new backup zip and prune old ones of the same kind. Returns its path."""
    if kind not in KEEP:
        raise BackupError("Unknown kind of backup")
    out = folder()
    out.mkdir(parents=True, exist_ok=True)
    stamp = groupme.now().strftime("%Y%m%d-%H%M%S")
    # Two backups in the same second get -2, -3...: always one more than the highest, so a pruned name isn't reused.
    same = [b["seq"] for b in listing(kind) if b["at"].strftime("%Y%m%d-%H%M%S") == stamp]
    path = out / (f"{kind}-{stamp}.zip" if not same else f"{kind}-{stamp}-{max(same) + 1}.zip")
    with tempfile.TemporaryDirectory(dir=out, prefix=".tmp-") as tmp:
        tmp = Path(tmp)
        partial = tmp / "backup.zip"
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as z:
            for i, (arc, src) in enumerate(_sources()):
                copy = tmp / f"{i}.db"
                _snapshot(src, copy)
                z.write(copy, arc)
        partial.replace(path)
    prune(kind)
    return path


def prune(kind: str) -> None:
    for old in listing(kind)[KEEP[kind]:]:
        (folder() / old["name"]).unlink(missing_ok=True)


def listing(kind: str | None = None) -> list[dict]:
    """Backups, newest first: [{name, kind, at (datetime), seq, size}]."""
    out = []
    if folder().is_dir():
        for p in folder().iterdir():
            match = NAME_RE.match(p.name)
            if match and p.is_file() and (kind is None or match[1] == kind):
                out.append({"name": p.name, "kind": match[1], "size": p.stat().st_size,
                            "at": datetime.strptime(match[2], "%Y%m%d-%H%M%S"),
                            "seq": int(match[3][1:]) if match[3] else 1})
    return sorted(out, key=lambda b: (b["at"], b["seq"]), reverse=True)


def path_of(name: str) -> Path | None:
    """The file for a backup name from a URL, or None. Only names we made are accepted."""
    if not NAME_RE.match(name):
        return None
    p = folder() / name
    return p if p.is_file() else None


# --- Nightly run ------------------------------------------------------------

LAST_ERROR = "backup:error"


def last_error() -> str:
    with db.connect() as conn:
        return db.get_setting(conn, LAST_ERROR) or ""


def run_due(moment: datetime | None = None) -> Path | None:
    """Make tonight's backup if it's after 3 am and today's hasn't been made yet."""
    moment = moment or groupme.now()
    if moment.hour < BACKUP_HOUR:
        return None
    today = moment.strftime("%Y%m%d")
    if any(b["at"].strftime("%Y%m%d") == today for b in listing("auto")):
        return None
    try:
        path = create("auto")
        error = ""
    except Exception as exc:
        log.exception("Nightly backup failed")
        path, error = None, f"{type(exc).__name__}: {exc}"[:300]
    with db.connect() as conn:
        db.set_setting(conn, LAST_ERROR, error)
    return path


async def run_forever() -> None:
    await asyncio.sleep(START_DELAY)
    while True:
        try:
            await asyncio.to_thread(run_due)
        except Exception:
            log.exception("Backup check failed")
        await asyncio.sleep(CHECK_EVERY)


# --- Restore (command line only, with the app stopped) ----------------------

def restore(zip_path: Path) -> list[str]:
    """Replace the live databases with the ones in a backup. Returns the files restored.

    Checks the whole zip first, and keeps a copy of what's being replaced as a pre-restore backup.
    Ward databases that aren't in the backup are removed, so a ward added later can't pick up an old
    ward's data. The app must be stopped.
    """
    zip_path = Path(zip_path)
    if not zip_path.is_file() or not zipfile.is_zipfile(zip_path):
        raise BackupError(f"{zip_path} isn't a backup file.")
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        if bad := [n for n in names if not ALLOWED.match(n)]:
            raise BackupError(f"Unexpected file in the backup: {bad[0]}")
        if "portal.db" not in names:
            raise BackupError("This backup has no portal.db.")
        folder().mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=folder(), prefix=".restore-") as tmp:
            staged = {}
            for i, name in enumerate(names):
                target = Path(tmp) / f"{i}.db"
                target.write_bytes(z.read(name))
                conn = sqlite3.connect(target)
                try:
                    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise BackupError(f"{name} in the backup is damaged.")
                except sqlite3.DatabaseError:
                    raise BackupError(f"{name} in the backup is damaged.") from None
                finally:
                    conn.close()
                staged[name] = target
            if _sources():
                create("pre-restore")
            for arc, live in [("portal.db", config.DB_PATH)] + [(n, config.DATA_DIR / n) for n in names if n != "portal.db"]:
                live.parent.mkdir(parents=True, exist_ok=True)
                for extra in ("-journal", "-wal", "-shm"):
                    Path(f"{live}{extra}").unlink(missing_ok=True)
                shutil.move(str(staged[arc]), live)
            wards = config.DATA_DIR / "wards"
            for p in wards.glob("ward-*.db") if wards.is_dir() else []:
                if f"wards/{p.name}" not in names:
                    p.unlink()
                    for extra in ("-journal", "-wal", "-shm"):
                        Path(f"{p}{extra}").unlink(missing_ok=True)
    db._ready.clear()
    return sorted(names)

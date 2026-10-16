"""Elders Quorum and Relief Society lessons, taught from the most recent general conference.

General conference is the first Sunday of April and October (and the Saturday before). The
lessons for a conference run from the Sunday after it to the Sunday before the next one.
"""
import sqlite3
from datetime import date, timedelta

from .reports import conference as gc

ORGS = {"eq": "Elders Quorum", "rs": "Relief Society"}


class LessonError(ValueError):
    pass


# --- Calendar ---------------------------------------------------------------

def conference_sunday(year: int, month: int) -> date:
    """The Sunday of general conference: the first Sunday of April or October."""
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7)


def next_conference(year: int, month: int) -> tuple[int, int]:
    return (year, 10) if month == 4 else (year + 1, 4)


def prev_conference(year: int, month: int) -> tuple[int, int]:
    return (year, 4) if month == 10 else (year - 1, 10)


def conference_for(day: date) -> tuple[int, int]:
    """The most recent general conference on or before `day` (its lessons are the ones taught then)."""
    year, month = (day.year, 10) if day.month >= 10 else (day.year, 4) if day.month >= 4 else (day.year - 1, 10)
    if day < conference_sunday(year, month):
        year, month = prev_conference(year, month)
    return year, month


def sundays(year: int, month: int) -> list[date]:
    """Sundays between a conference and the next one (both conference Sundays excluded)."""
    start = conference_sunday(year, month) + timedelta(days=7)
    end = conference_sunday(*next_conference(year, month))
    return [start + timedelta(weeks=i) for i in range((end - start).days // 7)]


def conference_name(year: int, month: int) -> str:
    return f"{gc.MONTH_NAMES[month]} {year}"


def parse_conf(text: str | None, today: date | None = None) -> tuple[int, int]:
    """'2026-10' -> (2026, 10); anything else means the conference being taught now."""
    try:
        year, month = (int(x) for x in (text or "").split("-"))
        if month in gc.MONTH_NAMES and 1990 < year < 2200:
            return year, month
    except ValueError:
        pass
    return conference_for(today or date.today())


# --- Conferences and talks --------------------------------------------------

def get_conference(conn: sqlite3.Connection, year: int, month: int) -> dict | None:
    row = conn.execute("SELECT * FROM conferences WHERE year = ? AND month = ?", (year, month)).fetchone()
    return dict(row) | {"name": conference_name(year, month)} if row else None


def list_conferences(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) | {"name": conference_name(r["year"], r["month"])} for r in conn.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM talks t WHERE t.conference_id = c.id) AS talks "
        "FROM conferences c ORDER BY year DESC, month DESC")]


def known_required(conn: sqlite3.Connection) -> set[str]:
    """Speakers this ward has seen marked as First Presidency or Twelve, plus the built-in list."""
    return gc.KNOWN_REQUIRED | {r[0] for r in conn.execute("SELECT DISTINCT speaker FROM talks WHERE required = 1")}


def store_conference(conn: sqlite3.Connection, conf: gc.Conference, source: str, username: str) -> int:
    """Save (or refresh) a conference's talks. Talks keep their ids, so lesson plans survive a refresh.

    A PDF has no callings, so refreshing from one keeps what the website said earlier.
    """
    row = conn.execute("SELECT id FROM conferences WHERE year = ? AND month = ?", (conf.year, conf.month)).fetchone()
    if row:
        conf_id = row["id"]
        conn.execute("UPDATE conferences SET source = ?, loaded_by = ?, loaded_at = datetime('now') WHERE id = ?",
                     (source, username, conf_id))
    else:
        conf_id = conn.execute("INSERT INTO conferences (year, month, source, loaded_by) VALUES (?, ?, ?, ?)",
                               (conf.year, conf.month, source, username)).lastrowid
    existing = {r["uri"]: dict(r) for r in conn.execute("SELECT * FROM talks WHERE conference_id = ?", (conf_id,))}
    seen = set()
    for pos, t in enumerate(conf.talks):
        if t.uri in seen:
            continue
        seen.add(t.uri)
        old = existing.get(t.uri)
        calling, required = t.calling, int(t.required)
        if old and not t.calling:   # keep callings (and the leader's ticks) that a PDF can't provide
            calling, required = old["calling"], old["required"]
        if old:
            conn.execute("UPDATE talks SET position = ?, session = ?, speaker = ?, calling = ?, title = ?, "
                         "summary = ?, kind = ?, required = ? WHERE id = ?",
                         (pos, t.session, t.speaker, calling, t.title, t.summary, t.kind, required, old["id"]))
        else:
            conn.execute("INSERT INTO talks (conference_id, position, session, speaker, calling, title, summary, "
                         "uri, kind, required) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (conf_id, pos, t.session, t.speaker, calling, t.title, t.summary, t.uri, t.kind, required))
    gone = [old["id"] for uri, old in existing.items() if uri not in seen]
    if gone:
        conn.execute(f"DELETE FROM talks WHERE id IN ({','.join('?' * len(gone))})", gone)
    return conf_id


def talks(conn: sqlite3.Connection, conference_id: int) -> list[dict]:
    return [dict(r) | {"url": f"{gc.SITE}{r['uri']}?lang=eng"} for r in conn.execute(
        "SELECT * FROM talks WHERE conference_id = ? ORDER BY position", (conference_id,))]


def set_required(conn: sqlite3.Connection, conference_id: int, speakers: set[str]) -> None:
    """Mark exactly these speakers' talks as by the First Presidency or the Twelve."""
    for r in conn.execute("SELECT id, speaker, kind FROM talks WHERE conference_id = ?", (conference_id,)).fetchall():
        conn.execute("UPDATE talks SET required = ? WHERE id = ?", (int(r["speaker"] in speakers), r["id"]))


def required_speakers(talk_rows: list[dict]) -> list[str]:
    """First Presidency and Twelve members who gave a talk, in conference order. Each must be studied."""
    out = []
    for t in talk_rows:
        if t["required"] and t["kind"] == "talk" and t["speaker"] not in out:
            out.append(t["speaker"])
    return out

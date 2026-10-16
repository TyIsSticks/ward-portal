"""Elders Quorum and Relief Society lessons, taught from the most recent general conference.

General conference is the first Sunday of April and October (and the Saturday before). The
lessons for a conference run from the Sunday after it to the Sunday before the next one.
"""
import sqlite3
from datetime import date, timedelta

from . import db
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


# --- Sundays ----------------------------------------------------------------
# Lessons are every Sunday except fifth Sundays (automatic, can be reopened) and Sundays the ward
# marks: stake conference or no second hour. Marks are ward-wide, so they apply to both plans.

STATUSES = {"lesson": "Lesson", "fifth_sunday": "Fifth Sunday", "stake_conference": "Stake conference",
            "no_second_hour": "No second hour"}


def auto_status(day: date) -> str:
    return "fifth_sunday" if day.day > 28 else "lesson"


def day_marks(conn: sqlite3.Connection, days: list[date]) -> dict[str, dict]:
    """{iso date: {"status", "note"}} for the given Sundays, marks applied over the defaults."""
    marks = {r["date"]: dict(r) for r in conn.execute("SELECT * FROM lesson_days")}
    out = {}
    for d in days:
        m = marks.get(d.isoformat())
        out[d.isoformat()] = {"status": m["status"] if m else auto_status(d), "note": m["note"] if m else ""}
    return out


def set_day(conn: sqlite3.Connection, iso: str, status: str, note: str = "") -> dict:
    """Mark a Sunday for the whole ward. A note (e.g. "Ward conference") goes with the mark."""
    try:
        day = date.fromisoformat(iso)
    except ValueError:
        raise LessonError("Not a date.") from None
    if day.weekday() != 6:
        raise LessonError("Lessons are on Sundays.")
    if status not in STATUSES or (status == "fifth_sunday" and day.day <= 28):
        raise LessonError("Unknown status.")
    note = " ".join(str(note or "").split())[:120]
    if status == "fifth_sunday" or (status == "lesson" and day.day <= 28 and not note):
        conn.execute("DELETE FROM lesson_days WHERE date = ?", (iso,))   # back to the default
    else:
        conn.execute("INSERT INTO lesson_days (date, status, note) VALUES (?, ?, ?) "
                     "ON CONFLICT(date) DO UPDATE SET status = excluded.status, note = excluded.note",
                     (iso, status, note))
    return day_marks(conn, [day])[iso]


# --- Plans ------------------------------------------------------------------

PLAN_NAMES = ORGS | {"ward": "Elders Quorum & Relief Society"}


def is_shared(conn: sqlite3.Connection) -> bool:
    """True when Elders Quorum and Relief Society teach the same talks each Sunday (one plan)."""
    return db.get_ward_setting(conn, "lessons_shared") == "1"


def set_shared(conn: sqlite3.Connection, shared: bool) -> None:
    """Switch between one shared plan and a plan per organization.

    The first time the plans are shared, the shared plan starts as a copy of Elders Quorum's.
    Each plan is kept, so switching back shows what was there before.
    """
    db.set_ward_setting(conn, "lessons_shared", "1" if shared else "0")
    if shared and not conn.execute("SELECT 1 FROM lesson_talks WHERE plan = 'ward' "
                                   "UNION SELECT 1 FROM lesson_notes WHERE plan = 'ward'").fetchone():
        conn.execute("INSERT INTO lesson_talks (plan, date, talk_id, position) "
                     "SELECT 'ward', date, talk_id, position FROM lesson_talks WHERE plan = 'eq'")
        conn.execute("INSERT INTO lesson_notes (plan, date, note) "
                     "SELECT 'ward', date, note FROM lesson_notes WHERE plan = 'eq'")


def plan_for(conn: sqlite3.Connection, org: str) -> str:
    return "ward" if is_shared(conn) else org


def plan_orgs(plan: str) -> list[str]:
    return list(ORGS) if plan == "ward" else [plan]


class VersionConflict(LessonError):
    pass


def _version(conn, plan: str, conference_id: int) -> int:
    row = conn.execute("SELECT version FROM lesson_boards WHERE plan = ? AND conference_id = ?",
                       (plan, conference_id)).fetchone()
    return row["version"] if row else 0


def plan_weeks(conn: sqlite3.Connection, plan: str, conf: dict) -> list[dict]:
    """Every Sunday of a conference's lessons: its ward-wide mark and this plan's talks and note."""
    days = sundays(conf["year"], conf["month"])
    marks = day_marks(conn, days)
    first, last = days[0].isoformat(), days[-1].isoformat()
    talk_ids: dict[str, list[int]] = {}
    for r in conn.execute("SELECT date, talk_id FROM lesson_talks WHERE plan = ? AND date BETWEEN ? AND ? "
                          "ORDER BY date, position", (plan, first, last)):
        talk_ids.setdefault(r["date"], []).append(r["talk_id"])
    notes = {r["date"]: r["note"] for r in conn.execute(
        "SELECT date, note FROM lesson_notes WHERE plan = ? AND date BETWEEN ? AND ?", (plan, first, last))}
    out = []
    for d in days:
        iso = d.isoformat()
        out.append({"date": iso, "status": marks[iso]["status"], "day_note": marks[iso]["note"],
                    "fifth": d.day > 28, "talks": talk_ids.get(iso, []), "note": notes.get(iso, "")})
    return out


def board_state(conn: sqlite3.Connection, plan: str, conf: dict, today: date | None = None) -> dict:
    """Everything the planning board needs, as JSON."""
    talk_rows = talks(conn, conf["id"])
    nxt = next_conference(conf["year"], conf["month"])
    return {
        "plan": plan,
        "plan_name": PLAN_NAMES[plan],
        "orgs": plan_orgs(plan),
        "org_names": ORGS,
        "conference": {"id": conf["id"], "key": f"{conf['year']}-{conf['month']:02d}", "name": conf["name"],
                       "next_name": conference_name(*nxt), "next_date": conference_sunday(*nxt).isoformat()},
        "today": (today or date.today()).isoformat(),
        "version": _version(conn, plan, conf["id"]),
        "statuses": STATUSES,
        "weeks": plan_weeks(conn, plan, conf),
        "talks": [{k: t[k] for k in ("id", "title", "speaker", "calling", "summary", "url", "kind", "session")}
                  | {"required": bool(t["required"])} for t in talk_rows],
        "required_speakers": required_speakers(talk_rows),
    }


def save_plan(conn: sqlite3.Connection, plan: str, conf: dict, weeks, version: int, username: str) -> int:
    """Replace a plan's talks and notes for one conference. Returns the new version."""
    if not isinstance(weeks, list):
        raise LessonError("weeks must be a list")
    current = _version(conn, plan, conf["id"])
    if version != current:
        raise VersionConflict("This plan was changed somewhere else.")
    valid_days = {d.isoformat() for d in sundays(conf["year"], conf["month"])}
    valid_talks = {r[0] for r in conn.execute("SELECT id FROM talks WHERE conference_id = ?", (conf["id"],))}
    rows, notes = [], []
    for w in weeks:
        if not isinstance(w, dict) or w.get("date") not in valid_days:
            raise LessonError("Unknown Sunday.")
        ids = [int(t) for t in w.get("talks") or []]
        if any(t not in valid_talks for t in ids):
            raise LessonError("Unknown talk.")
        rows += [(plan, w["date"], t, i) for i, t in enumerate(dict.fromkeys(ids))]
        note = " ".join(str(w.get("note") or "").split())[:200]
        if note:
            notes.append((plan, w["date"], note))
    first, last = min(valid_days), max(valid_days)
    conn.execute("DELETE FROM lesson_talks WHERE plan = ? AND date BETWEEN ? AND ?", (plan, first, last))
    conn.execute("DELETE FROM lesson_notes WHERE plan = ? AND date BETWEEN ? AND ?", (plan, first, last))
    conn.executemany("INSERT INTO lesson_talks (plan, date, talk_id, position) VALUES (?, ?, ?, ?)", rows)
    conn.executemany("INSERT INTO lesson_notes (plan, date, note) VALUES (?, ?, ?)", notes)
    conn.execute("INSERT INTO lesson_boards (plan, conference_id, version, updated_by) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(plan, conference_id) DO UPDATE SET version = excluded.version, "
                 "updated_by = excluded.updated_by, updated_at = datetime('now')",
                 (plan, conf["id"], current + 1, username))
    return current + 1

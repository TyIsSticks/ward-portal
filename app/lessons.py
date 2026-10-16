"""Elders Quorum and Relief Society lessons, taught from the most recent general conference.

General conference is the first Sunday of April and October (and the Saturday before). The
lessons for a conference run from the Sunday after it to the Sunday before the next one.
"""
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone

from icalendar import Calendar, Event

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
    orgs = plan_orgs(plan)
    teaching: dict[tuple, list[int]] = {}
    for r in conn.execute("SELECT lt.org, lt.date, lt.teacher_id FROM lesson_teachers lt JOIN teachers t ON t.id = lt.teacher_id "
                          "WHERE lt.date BETWEEN ? AND ? ORDER BY t.name COLLATE NOCASE", (first, last)):
        teaching.setdefault((r["org"], r["date"]), []).append(r["teacher_id"])
    out = []
    for d in days:
        iso = d.isoformat()
        out.append({"date": iso, "status": marks[iso]["status"], "day_note": marks[iso]["note"],
                    "fifth": d.day > 28, "talks": talk_ids.get(iso, []), "note": notes.get(iso, ""),
                    "teachers": {o: teaching.get((o, iso), []) for o in orgs}})
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
        "teachers": {o: list_teachers(conn, o) for o in plan_orgs(plan)},
        "roster": {o: roster(conn, o) for o in plan_orgs(plan)},
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
    orgs = plan_orgs(plan)
    valid_teachers = {o: {r[0] for r in conn.execute("SELECT id FROM teachers WHERE org = ?", (o,))} for o in orgs}
    rows, notes, teaching = [], [], []
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
        assigned = w.get("teachers") or {}
        if not isinstance(assigned, dict):
            raise LessonError("teachers must be an object")
        for o in orgs:
            ids = [int(t) for t in assigned.get(o) or []]
            if any(t not in valid_teachers[o] for t in ids):
                raise LessonError("Unknown teacher.")
            teaching += [(o, w["date"], t) for t in dict.fromkeys(ids)]
    first, last = min(valid_days), max(valid_days)
    conn.execute("DELETE FROM lesson_talks WHERE plan = ? AND date BETWEEN ? AND ?", (plan, first, last))
    conn.execute("DELETE FROM lesson_notes WHERE plan = ? AND date BETWEEN ? AND ?", (plan, first, last))
    conn.executemany("INSERT INTO lesson_talks (plan, date, talk_id, position) VALUES (?, ?, ?, ?)", rows)
    conn.executemany("INSERT INTO lesson_notes (plan, date, note) VALUES (?, ?, ?)", notes)
    for o in orgs:   # teachers belong to an organization, whichever plan it's using
        conn.execute("DELETE FROM lesson_teachers WHERE org = ? AND date BETWEEN ? AND ?", (o, first, last))
    conn.executemany("INSERT INTO lesson_teachers (org, date, teacher_id) VALUES (?, ?, ?)", teaching)
    conn.execute("INSERT INTO lesson_boards (plan, conference_id, version, updated_by) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(plan, conference_id) DO UPDATE SET version = excluded.version, "
                 "updated_by = excluded.updated_by, updated_at = datetime('now')",
                 (plan, conf["id"], current + 1, username))
    return current + 1


# --- Teachers ---------------------------------------------------------------

ORG_GENDER = {"eq": "M", "rs": "F"}


def _display(name: str) -> str:
    """'Last, First Middle' -> 'First Middle Last' (roster names)."""
    if "," not in name:
        return name
    last, given = (part.strip() for part in name.split(",", 1))
    return f"{given} {last}".strip()


def roster(conn: sqlite3.Connection, org: str) -> list[dict]:
    """People who could teach in an organization (brothers for EQ, sisters for RS), by last name."""
    return [{"id": r["id"], "name": _display(r["name"])} for r in conn.execute(
        "SELECT id, name FROM people WHERE active = 1 AND gender = ? ORDER BY name COLLATE NOCASE",
        (ORG_GENDER[org],))]


def list_teachers(conn: sqlite3.Connection, org: str, include_inactive: bool = True) -> list[dict]:
    rows = conn.execute(
        "SELECT t.*, p.active AS person_active, "
        "(SELECT COUNT(*) FROM lesson_teachers lt WHERE lt.teacher_id = t.id) AS lessons, "
        "(SELECT MIN(date) FROM lesson_teachers lt WHERE lt.teacher_id = t.id "
        " AND date >= date('now', 'localtime')) AS next_date "
        "FROM teachers t LEFT JOIN people p ON p.id = t.person_id WHERE t.org = ? "
        + ("" if include_inactive else "AND t.active = 1 ") + "ORDER BY t.active DESC, t.name COLLATE NOCASE", (org,))
    return [{"id": r["id"], "org": r["org"], "name": r["name"], "person_id": r["person_id"],
             "active": bool(r["active"]), "moved_out": r["person_active"] == 0, "lessons": r["lessons"],
             "next_date": r["next_date"]} for r in rows]


def get_teacher(conn: sqlite3.Connection, teacher_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM teachers WHERE id = ?", (teacher_id,)).fetchone()
    return dict(row) if row else None


def add_teacher(conn: sqlite3.Connection, org: str, name: str = "", person_id: int | None = None) -> dict:
    """Add a teacher from the roster (person_id) or by name. Adding someone again reactivates them."""
    if org not in ORGS:
        raise LessonError("Unknown organization.")
    if person_id:
        person = conn.execute("SELECT id, name FROM people WHERE id = ?", (person_id,)).fetchone()
        if person is None:
            raise LessonError("That person is not on the roster.")
        name = _display(person["name"])
        existing = conn.execute("SELECT id FROM teachers WHERE org = ? AND person_id = ?", (org, person_id)).fetchone()
    else:
        name = " ".join(name.split())[:80]
        if not name:
            raise LessonError("Give the teacher a name.")
        existing = conn.execute("SELECT id FROM teachers WHERE org = ? AND person_id IS NULL "
                                "AND name = ? COLLATE NOCASE", (org, name)).fetchone()
    if existing:
        conn.execute("UPDATE teachers SET active = 1 WHERE id = ?", (existing["id"],))
        teacher_id = existing["id"]
    else:
        teacher_id = conn.execute("INSERT INTO teachers (org, name, person_id, feed_token) VALUES (?, ?, ?, ?)",
                                  (org, name, person_id or None, secrets.token_urlsafe(24))).lastrowid
    return next(t for t in list_teachers(conn, org) if t["id"] == teacher_id)


def set_teacher_active(conn: sqlite3.Connection, teacher_id: int, active: bool) -> None:
    conn.execute("UPDATE teachers SET active = ? WHERE id = ?", (int(active), teacher_id))


def remove_teacher(conn: sqlite3.Connection, teacher_id: int) -> str:
    """Delete a teacher who never taught; otherwise drop their future lessons and keep them inactive."""
    if conn.execute("SELECT 1 FROM lesson_teachers WHERE teacher_id = ? AND date < date('now', 'localtime')",
                    (teacher_id,)).fetchone():
        conn.execute("DELETE FROM lesson_teachers WHERE teacher_id = ? AND date >= date('now', 'localtime')",
                     (teacher_id,))
        set_teacher_active(conn, teacher_id, False)
        return "inactive"
    conn.execute("DELETE FROM teachers WHERE id = ?", (teacher_id,))
    return "deleted"


# --- Dashboard ----------------------------------------------------------------

SOON_DAYS = 14   # how far ahead the dashboard nags about missing talks and teachers


def dashboard(conn: sqlite3.Connection, today: date | None = None) -> dict:
    """The next lesson for each plan, how the First Presidency and Twelve are going, and to-dos."""
    today = today or date.today()
    year, month = conference_for(today)
    record = get_conference(conn, year, month)
    out = {"conf_name": conference_name(year, month), "conf_key": f"{year}-{month:02d}", "loaded": bool(record),
           "plans": [], "todo": []}
    if record is None:
        out["todo"].append({"text": f"Load the {out['conf_name']} conference talks for lesson planning",
                            "cta": "Load", "href": f"/lessons/talks?conf={out['conf_key']}"})
        return out
    by_id = {t["id"]: t for t in talks(conn, record["id"])}
    required = required_speakers(list(by_id.values()))
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM teachers")}
    for plan in (["ward"] if is_shared(conn) else list(ORGS)):
        weeks = plan_weeks(conn, plan, record)
        lessons = [w for w in weeks if w["status"] == "lesson"]
        covered = {by_id[t]["speaker"] for w in lessons for t in w["talks"]
                   if by_id[t]["required"] and by_id[t]["kind"] == "talk"}
        ahead = [w for w in lessons if w["date"] >= today.isoformat()]
        nxt = ahead[0] if ahead else None
        short = "EQ & RS" if plan == "ward" else PLAN_NAMES[plan]
        out["plans"].append({
            "plan": plan, "name": short, "abbr": {"eq": "EQ", "rs": "RS"}.get(plan, "EQ & RS"),
            "covered": len(covered & set(required)), "total": len(required),
            "next": nxt and {"date": date.fromisoformat(nxt["date"]),
                             "talks": [by_id[t]["title"] for t in nxt["talks"]],
                             "teachers": {o: [names[t] for t in ids] for o, ids in nxt["teachers"].items()}},
        })
        href = f"/lessons/{plan}?conf={out['conf_key']}"
        for w in ahead:
            day = date.fromisoformat(w["date"])
            if (day - today).days > SOON_DAYS:
                break
            when = f"{day:%a, %b} {day.day}"
            if not w["talks"]:
                out["todo"].append({"text": f"Pick a talk for {short} on {when}", "cta": "Plan", "href": href})
            for o, ids in w["teachers"].items():
                if not ids:
                    out["todo"].append({"text": f"Find {'an' if o == 'eq' else 'a'} {ORGS[o]} teacher for {when}",
                                        "cta": "Plan", "href": href})
        missing = len(required) - len(covered & set(required))
        open_ahead = sum(1 for w in ahead if not w["talks"])
        if missing > open_ahead:
            out["todo"].append({"text": f"{short}: {missing} First Presidency and Twelve speaker{'s' * (missing != 1)} "
                                        f"left but only {open_ahead} open Sunday{'s' * (open_ahead != 1)}. "
                                        "Combine some lessons",
                                "cta": "Plan", "href": href})
    return out


# --- Calendar feeds -------------------------------------------------------------
# Same idea as the birthday calendar: a private link that phones subscribe to. Each organization
# has a feed of its whole schedule (one secret per ward), and each teacher has their own.

def feed_token(conn: sqlite3.Connection) -> str:
    token = db.get_ward_setting(conn, "lessons_feed_token")
    if not token:
        token = secrets.token_urlsafe(24)
        db.set_ward_setting(conn, "lessons_feed_token", token)
    return token


def new_feed_token(conn: sqlite3.Connection) -> None:
    db.set_ward_setting(conn, "lessons_feed_token", secrets.token_urlsafe(24))


def new_teacher_token(conn: sqlite3.Connection, teacher_id: int) -> None:
    conn.execute("UPDATE teachers SET feed_token = ? WHERE id = ?", (secrets.token_urlsafe(24), teacher_id))


def teacher_by_token(conn: sqlite3.Connection, token: str) -> dict | None:
    row = conn.execute("SELECT * FROM teachers WHERE feed_token = ?", (token,)).fetchone()
    return dict(row) if row else None


def _schedule(conn: sqlite3.Connection, org: str) -> list[dict]:
    """Every planned Sunday for an organization, across all loaded conferences."""
    plan = plan_for(conn, org)
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM teachers")}
    out = []
    for conf in list_conferences(conn):
        by_id = {t["id"]: t for t in talks(conn, conf["id"])}
        for w in plan_weeks(conn, plan, conf):
            w = dict(w, talks=[by_id[t] for t in w["talks"] if t in by_id], teacher_ids=w["teachers"].get(org, []))
            w["teacher_names"] = [names[t] for t in w["teacher_ids"]]
            out.append(w)
    return out


def _describe(w: dict, teacher_line: bool = True) -> str:
    lines = []
    if teacher_line and w["teacher_names"]:
        lines.append(f"Teacher: {', '.join(w['teacher_names'])}")
    if w["note"]:
        lines.append(f"Note: {w['note']}")
    for t in w["talks"]:
        lines += ["", f"{t['title']} ({t['speaker']})"] + ([t["summary"]] if t["summary"] else []) + [t["url"]]
    return "\n".join(lines).strip()


def _calendar(name: str) -> Calendar:
    cal = Calendar()
    cal.add("prodid", "-//ward-portal//lessons//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", name)
    cal.add("refresh-interval;value=duration", "PT6H")
    cal.add("x-published-ttl", "PT6H")
    return cal


def _event(uid: str, day: str, summary: str, description: str) -> Event:
    ev = Event()
    ev.add("uid", f"{uid}@ward-portal")   # stable, so calendar apps update events in place
    ev.add("summary", summary)
    ev.add("dtstart", date.fromisoformat(day))
    ev.add("dtend", date.fromisoformat(day) + timedelta(days=1))
    ev.add("dtstamp", datetime.now(timezone.utc))
    ev.add("transp", "TRANSPARENT")
    if description:
        ev.add("description", description)
    return ev


def org_feed(conn: sqlite3.Connection, org: str, ward_name: str) -> bytes:
    """An organization's whole schedule: each lesson with its talks and teacher, and Sundays without one."""
    short = {"eq": "EQ", "rs": "RS"}[org]
    cal = _calendar(f"{ward_name} {ORGS[org]} lessons")
    for w in _schedule(conn, org):
        if w["status"] == "lesson" and (w["talks"] or w["teacher_ids"] or w["note"]):
            titles = " + ".join(t["title"] for t in w["talks"]) or "talk not picked yet"
            who = f" ({', '.join(w['teacher_names'])})" if w["teacher_names"] else ""
            cal.add_component(_event(f"{org}-{w['date']}", w["date"], f"{short} lesson: {titles}{who}", _describe(w)))
        elif w["status"] in ("stake_conference", "no_second_hour"):
            label = STATUSES[w["status"]] + (f": {w['day_note']}" if w["day_note"] else "")
            cal.add_component(_event(f"{org}-{w['date']}", w["date"], f"No {short} lesson ({label})", ""))
    return cal.to_ical()


def teacher_feed(conn: sqlite3.Connection, teacher: dict, ward_name: str) -> bytes:
    """Just the Sundays one teacher teaches."""
    org = teacher["org"]
    cal = _calendar(f"{ORGS[org]} lessons: {teacher['name']}")
    for w in _schedule(conn, org):
        if w["status"] != "lesson" or teacher["id"] not in w["teacher_ids"]:
            continue
        titles = " + ".join(t["title"] for t in w["talks"]) or "talk not picked yet"
        others = [n for i, n in zip(w["teacher_ids"], w["teacher_names"]) if i != teacher["id"]]
        desc = _describe(w, teacher_line=False)
        if others:
            desc = f"Teaching with {', '.join(others)}\n{desc}"
        cal.add_component(_event(f"t{teacher['id']}-{w['date']}", w["date"], f"Teach {ORGS[org]}: {titles}",
                                 f"{ward_name}\n{desc}".strip()))
    return cal.to_ical()

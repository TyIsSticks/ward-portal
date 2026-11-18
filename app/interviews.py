"""Ministering interviews: who has had their quarterly interview, and notes to remember next time.

Companionships are rebuilt on every LCR import, so an interview isn't tied to one. It's tied to the
people interviewed (the ministers). A companionship's history is every interview that included any of
its current ministers, which carries on sensibly when a companion changes.

Each interview keeps two free-text fields: what was talked about, and what to follow up on next time.
Both are for leaders only; they're not shown on any printable page or sent anywhere.
"""
import sqlite3
from datetime import date, timedelta

from . import ministering as m

MAX_TEXT = 10000
REMIND_DAYS = 35    # the to-do list starts asking this long before a quarter ends
QUARTERS = ["Jan–Mar", "Apr–Jun", "Jul–Sep", "Oct–Dec"]


class InterviewError(Exception):
    pass


def quarter(day: date) -> dict:
    """The calendar quarter a date falls in: {start, end, label}."""
    q = (day.month - 1) // 3
    start = date(day.year, q * 3 + 1, 1)
    end = date(day.year + (q == 3), (q * 3 + 3) % 12 + 1, 1) - timedelta(days=1)
    return {"start": start, "end": end, "label": f"{QUARTERS[q]} {day.year}"}


# --- Reading ----------------------------------------------------------------

def _by_person(conn: sqlite3.Connection) -> dict[int, list[dict]]:
    """Every interview, newest first, under each person who was in it."""
    out: dict[int, list[dict]] = {}
    for r in conn.execute(
            "SELECT i.id, i.held_on, i.follow_up, i.notes, im.person_id FROM interviews i "
            "JOIN interview_ministers im ON im.interview_id = i.id ORDER BY i.held_on DESC, i.id DESC"):
        out.setdefault(r["person_id"], []).append(dict(r))
    return out


def groups(conn: sqlite3.Connection, org: str, today: date) -> dict:
    """Each companionship in the current LCR import with where its interview stands this quarter.

    A companionship is done once every one of its ministers is in an interview held this quarter,
    so a new companion means it's due again. Returns {"current", "quarter", "districts", "total", "done"}.
    """
    q = quarter(today)
    current = m.current_import(conn, org)
    out = {"current": current, "quarter": q, "districts": [], "total": 0, "done": 0,
           "days_left": (q["end"] - today).days}
    if current is None:
        return out
    by_person = _by_person(conn)
    districts = m.structure(conn, current["id"])
    people = m.people(conn, {p for d in districts for g in d["groups"] for p in g["ministers"]})
    lo, hi = q["start"].isoformat(), q["end"].isoformat()
    for d in districts:
        rows = []
        for g in d["groups"]:
            if not g["ministers"]:
                continue
            mine = [i for p in g["ministers"] for i in by_person.get(p, [])]
            last = max(mine, key=lambda i: (i["held_on"], i["id"]), default=None)
            this_q = {p: max((i["held_on"] for i in by_person.get(p, []) if lo <= i["held_on"] <= hi), default=None)
                      for p in g["ministers"]}
            done = all(this_q.values())
            rows.append({"ministers": [people[p] for p in g["ministers"] if p in people],
                         "minister_ids": g["ministers"], "assigned": len(g["assigned"]),
                         "done": done, "done_on": max(this_q.values(), default=None) if done else None,
                         "last": last, "count": len({i["id"] for i in mine})})
        if rows:
            out["districts"].append({"name": d["name"], "supervisor": d["supervisor"], "groups": rows})
            out["total"] += len(rows)
            out["done"] += sum(r["done"] for r in rows)
    return out


def due_soon(conn: sqlite3.Connection, org: str, today: date) -> int:
    """How many companionships still need an interview, once the quarter is nearly over (else 0)."""
    g = groups(conn, org, today)
    return g["total"] - g["done"] if g["days_left"] <= REMIND_DAYS else 0


def _people_of(conn: sqlite3.Connection, interview_ids: list[int]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {i: [] for i in interview_ids}
    if interview_ids:
        marks = ",".join("?" * len(interview_ids))
        for r in conn.execute(f"SELECT interview_id, person_id FROM interview_ministers WHERE interview_id IN ({marks}) "
                              "ORDER BY person_id", interview_ids):
            out[r["interview_id"]].append(r["person_id"])
    return out


def _attach(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
    ids = [r["id"] for r in rows]
    who = _people_of(conn, ids)
    everyone = m.people(conn, {p for ps in who.values() for p in ps})
    return [dict(r) | {"ministers": [everyone[p] for p in who[r["id"]] if p in everyone]} for r in rows]


def get(conn: sqlite3.Connection, interview_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM interviews WHERE id = ?", (interview_id,)).fetchone()
    return _attach(conn, [row])[0] if row else None


def history(conn: sqlite3.Connection, minister_ids: list[int], exclude: int | None = None) -> list[dict]:
    """Past interviews that included any of these ministers, newest first."""
    if not minister_ids:
        return []
    marks = ",".join("?" * len(minister_ids))
    rows = conn.execute(
        f"SELECT * FROM interviews WHERE id != ? AND id IN (SELECT interview_id FROM interview_ministers "
        f"WHERE person_id IN ({marks})) ORDER BY held_on DESC, id DESC", [exclude or 0, *minister_ids]).fetchall()
    return _attach(conn, rows)


def recent(conn: sqlite3.Connection, org: str, limit: int = 12) -> list[dict]:
    return _attach(conn, conn.execute("SELECT * FROM interviews WHERE org = ? ORDER BY held_on DESC, id DESC LIMIT ?",
                                      (org, limit)).fetchall())


# --- Writing ----------------------------------------------------------------

def _clean(conn: sqlite3.Connection, held_on: str, minister_ids: list[int], notes: str, follow_up: str):
    try:
        day = date.fromisoformat(held_on)
    except ValueError:
        raise InterviewError("Pick the date of the interview.") from None
    if day > date.today() + timedelta(days=1):
        raise InterviewError("That date is in the future.")
    ids = list(dict.fromkeys(minister_ids))
    if not ids:
        raise InterviewError("Pick who was interviewed.")
    valid = {r[0] for r in conn.execute(f"SELECT id FROM people WHERE id IN ({','.join('?' * len(ids))})", ids)}
    if set(ids) - valid:
        raise InterviewError("Unknown person.")
    notes, follow_up = notes.strip(), follow_up.strip()
    if len(notes) > MAX_TEXT or len(follow_up) > MAX_TEXT:
        raise InterviewError(f"Notes can be up to {MAX_TEXT:,} characters.")
    return day.isoformat(), ids, notes, follow_up


def create(conn: sqlite3.Connection, org: str, held_on: str, minister_ids: list[int], notes: str,
           follow_up: str, user: str) -> int:
    m.check_org(org)
    held_on, ids, notes, follow_up = _clean(conn, held_on, minister_ids, notes, follow_up)
    interview_id = conn.execute(
        "INSERT INTO interviews (org, held_on, notes, follow_up, created_by, updated_by) VALUES (?, ?, ?, ?, ?, ?)",
        (org, held_on, notes, follow_up, user, user)).lastrowid
    conn.executemany("INSERT INTO interview_ministers (interview_id, person_id) VALUES (?, ?)",
                     [(interview_id, p) for p in ids])
    return interview_id


def update(conn: sqlite3.Connection, interview_id: int, held_on: str, minister_ids: list[int], notes: str,
           follow_up: str, user: str) -> None:
    if conn.execute("SELECT 1 FROM interviews WHERE id = ?", (interview_id,)).fetchone() is None:
        raise InterviewError("Interview not found.")
    held_on, ids, notes, follow_up = _clean(conn, held_on, minister_ids, notes, follow_up)
    conn.execute("UPDATE interviews SET held_on = ?, notes = ?, follow_up = ?, updated_by = ?, "
                 "updated_at = datetime('now') WHERE id = ?", (held_on, notes, follow_up, user, interview_id))
    conn.execute("DELETE FROM interview_ministers WHERE interview_id = ?", (interview_id,))
    conn.executemany("INSERT INTO interview_ministers (interview_id, person_id) VALUES (?, ?)",
                     [(interview_id, p) for p in ids])


def delete(conn: sqlite3.Connection, interview_id: int) -> None:
    if conn.execute("DELETE FROM interviews WHERE id = ?", (interview_id,)).rowcount != 1:
        raise InterviewError("Interview not found.")

"""LCR "Birthday List" report: parse the PDF export, sync the roster, serve an iCalendar feed.

The export is a table with columns Birthday ("3 Jan"), Name ("Last, First Middle"), Age,
Phone Number and Street Address. Only name and month/day are kept.
"""
import hashlib
import io
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone

import pdfplumber
from icalendar import Calendar, Event

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


class ReportError(ValueError):
    """The uploaded file isn't a birthday list we can read."""


@dataclass(frozen=True, order=True)
class Birthday:
    month: int
    day: int
    name: str

    @property
    def display_name(self) -> str:
        return display_name(self.name)


def display_name(name: str) -> str:
    """'Last, First Middle' -> 'First Middle Last'."""
    if "," not in name:
        return name
    last, given = (part.strip() for part in name.split(",", 1))
    return f"{given} {last}".strip()


def _clean(cell) -> str:
    return " ".join(str(cell or "").split())


def _parse_birthday(text: str) -> tuple[int, int] | None:
    parts = text.split()
    if len(parts) != 2 or not parts[0].isdigit():
        return None
    month = MONTHS.get(parts[1][:3].lower())
    day = int(parts[0])
    if month is None:
        return None
    try:
        date(2000, month, day)  # leap year, so Feb 29 is valid
    except ValueError:
        return None
    return month, day


def parse_rows(tables: list[list[list]]) -> list[Birthday]:
    """Turn extracted table rows into birthdays. Columns are found by header name."""
    col_bday = col_name = None
    found: set[Birthday] = set()

    for table in tables:
        for row in table:
            cells = [_clean(c) for c in row]
            lowered = [c.lower() for c in cells]
            if "birthday" in lowered and "name" in lowered:
                col_bday, col_name = lowered.index("birthday"), lowered.index("name")
                continue
            if col_bday is None or max(col_bday, col_name) >= len(cells):
                continue
            md = _parse_birthday(cells[col_bday])
            name = cells[col_name]
            if md and name:
                found.add(Birthday(md[0], md[1], name))

    if col_bday is None:
        raise ReportError("Couldn't find the Birthday/Name columns. Is this the LCR Birthday List?")
    if not found:
        raise ReportError("Found the table but no birthdays in it.")
    return sorted(found)


def parse_pdf(data: bytes) -> list[Birthday]:
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:
        raise ReportError("That file couldn't be opened as a PDF.") from exc
    with pdf:
        first_text = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
        if "birthday list" not in first_text.lower():
            raise ReportError("This doesn't look like the LCR Birthday List report.")
        tables = [t for page in pdf.pages for t in page.extract_tables()]
    return parse_rows(tables)


def load(conn: sqlite3.Connection) -> list[Birthday]:
    rows = conn.execute("SELECT month, day, name FROM birthdays").fetchall()
    return sorted(Birthday(r["month"], r["day"], r["name"]) for r in rows)


def sync(conn: sqlite3.Connection, incoming: list[Birthday]) -> tuple[list[Birthday], list[Birthday]]:
    """Add new birthdays and flag (never delete) ones missing from the upload.

    Returns (added, newly_missing). Flagged people stay on the calendar until someone removes them
    or marks them to keep; if they show up in a later upload, the flag clears.
    """
    current = set(load(conn))
    new = set(incoming)
    added = sorted(new - current)
    already_flagged = set(missing(conn, include_kept=True))
    newly_missing = sorted(current - new - already_flagged)
    conn.executemany("INSERT INTO birthdays (name, month, day) VALUES (?, ?, ?)",
                     [(b.name, b.month, b.day) for b in added])
    conn.executemany("UPDATE birthdays SET missing_since = datetime('now') WHERE name = ? AND month = ? AND day = ?",
                     [(b.name, b.month, b.day) for b in newly_missing])
    conn.executemany("UPDATE birthdays SET missing_since = NULL, kept = 0 WHERE name = ? AND month = ? AND day = ?",
                     [(b.name, b.month, b.day) for b in new & current])
    return added, newly_missing


def missing(conn: sqlite3.Connection, include_kept: bool = False) -> list[Birthday]:
    """Birthdays that weren't in the latest upload (probably moved out) and still need a decision."""
    sql = "SELECT month, day, name FROM birthdays WHERE missing_since IS NOT NULL"
    if not include_kept:
        sql += " AND kept = 0"
    return sorted(Birthday(r["month"], r["day"], r["name"]) for r in conn.execute(sql))


def resolve_missing(conn: sqlite3.Connection, birthday_id: int, action: str) -> None:
    """action: 'remove' deletes it from the calendar; 'keep' keeps it and stops flagging it."""
    if action == "remove":
        conn.execute("DELETE FROM birthdays WHERE id = ? AND missing_since IS NOT NULL", (birthday_id,))
    elif action == "keep":
        conn.execute("UPDATE birthdays SET kept = 1 WHERE id = ?", (birthday_id,))
    else:
        raise ValueError(action)


def build_ics(birthdays: list[Birthday], calendar_name: str) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//ward-portal//birthdays//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", calendar_name)
    # Hints for clients that honor them (Google mostly sets its own schedule).
    cal.add("refresh-interval;value=duration", "PT12H")
    cal.add("x-published-ttl", "PT12H")

    stamp = datetime.now(timezone.utc)
    for b in birthdays:
        ev = Event()
        # Stable UID so calendar apps update events in place rather than duplicating them.
        uid = hashlib.sha256(f"{b.name}|{b.month}|{b.day}".encode()).hexdigest()[:24]
        ev.add("uid", f"{uid}@ward-portal")
        ev.add("summary", f"🎂 {b.display_name}")
        ev.add("dtstart", date(2000, b.month, b.day))
        if (b.month, b.day) == (2, 29):
            ev.add("rrule", {"freq": "yearly", "bymonth": 2, "bymonthday": -1})
        else:
            ev.add("rrule", {"freq": "yearly"})
        ev.add("dtstamp", stamp)
        ev.add("transp", "TRANSPARENT")
        cal.add_component(ev)
    return cal.to_ical()

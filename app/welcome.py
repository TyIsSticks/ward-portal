"""Move-in welcome checklist: introduced in sacrament meeting, ministers assigned, calling considered.

A person lands on the list when the Member List shows them moving in (see ministering.sync_people).
Two steps are ticked by hand. "Ministers assigned" is never stored: it's read from the current
LCR imports, so it ticks itself once an Elders Quorum or Relief Society import shows ministers.
"""
import sqlite3

from . import ministering as m

# The steps ticked by hand: column on the welcome table -> label.
STEPS = {"introduced": "Introduced in sacrament meeting", "calling": "Calling considered"}
ADULT = 18   # ministers are assigned from this age (same cutoff as Who ministers to whom)


class WelcomeError(Exception):
    pass


def ensure(conn: sqlite3.Connection) -> None:
    """Put everyone still tagged "New move-in" on the list (people who moved in before it existed)."""
    conn.execute(
        "INSERT OR IGNORE INTO welcome (person_id) "
        "SELECT p.id FROM people p JOIN person_tags t ON t.person_id = p.id "
        "WHERE p.active = 1 AND t.tag = 'New move-in'")


def _ministered(conn: sqlite3.Connection) -> tuple[bool, set[int]]:
    """(is there a current import at all, everyone who has ministers in one)."""
    imports = [c for c in (m.current_import(conn, org) for org in m.ORGS) if c]
    ids: set[int] = set()
    for c in imports:
        ids |= {p for p, ministers in m.ministers_of(conn, c["id"]).items() if ministers}
    return bool(imports), ids


def checklist(conn: sqlite3.Connection) -> dict:
    """{open: [...], done: [...]} for everyone currently in the ward. Dismissed people are left out.

    Each row has the person, the stamps for the hand-ticked steps, and `ministers`: "yes", "no"
    (an adult with none), "na" (under 18) or "unknown" (no LCR import yet).
    """
    ensure(conn)
    have_import, ministered = _ministered(conn)
    people = m.people(conn)
    rows = {"open": [], "done": []}
    for r in conn.execute("SELECT * FROM welcome WHERE dismissed_at IS NULL ORDER BY added_at DESC, person_id DESC"):
        person = people.get(r["person_id"])
        if person is None:   # moved out again
            continue
        if (person["age"] or ADULT) < ADULT:
            ministers = "na"
        elif not have_import:
            ministers = "unknown"
        else:
            ministers = "yes" if r["person_id"] in ministered else "no"
        row = {"person": person, "added_at": r["added_at"], "introduced_at": r["introduced_at"],
               "calling_at": r["calling_at"], "ministers": ministers}
        complete = row["introduced_at"] and row["calling_at"] and ministers in ("yes", "na")
        rows["done" if complete else "open"].append(row)
    rows["done"] = rows["done"][:20]
    return rows


def summary(conn: sqlite3.Connection) -> dict:
    """Counts for the dashboard to-do list."""
    open_rows = checklist(conn)["open"]
    return {"open": len(open_rows),
            "no_ministers": sum(r["ministers"] == "no" for r in open_rows),
            "to_welcome": sum(not (r["introduced_at"] and r["calling_at"]) for r in open_rows)}


def set_step(conn: sqlite3.Connection, person_id: int, step: str, done: bool) -> None:
    if step not in STEPS:
        raise WelcomeError("Unknown step")
    column = f"{step}_at"   # safe: `step` is one of STEPS
    stamp = "datetime('now')" if done else "NULL"
    ensure(conn)
    if conn.execute(f"UPDATE welcome SET {column} = {stamp} WHERE person_id = ?", (person_id,)).rowcount != 1:
        raise WelcomeError("Unknown person")


def dismiss(conn: sqlite3.Connection, person_id: int, undo: bool = False) -> None:
    stamp = "NULL" if undo else "datetime('now')"
    if conn.execute(f"UPDATE welcome SET dismissed_at = {stamp} WHERE person_id = ?", (person_id,)).rowcount != 1:
        raise WelcomeError("Unknown person")

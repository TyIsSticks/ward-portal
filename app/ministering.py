"""Ministering: ward roster, layouts (imported LCR snapshots and editable drafts), history and diffs.

A layout's structure is districts -> companionships -> members, where each member is a person
with a role: 'minister' (in the companionship) or 'assigned' (ministered to by it).
"""
import difflib
import json
import sqlite3
from datetime import date

from .reports import directory, ministering as report
from .reports.birthdays import Birthday, display_name

PRIORITY_TAGS = ["New move-in", "New member", "Needs extra care", "Returning", "Limited contact"]
# Tags that should never be left without ministers.
NEEDS_MINISTERS_TAGS = {"New move-in", "New member", "Needs extra care", "Returning"}
STATUSES = ("draft", "proposed", "approved")

# Elders quorum and Relief Society keep separate assignments in LCR, so each has its own layouts.
ORGS = {"eq": "Elders Quorum", "rs": "Relief Society"}
ORG_GENDER = {"eq": "M", "rs": "F"}  # who ministers in each


def check_org(org: str) -> str:
    if org not in ORGS:
        raise LayoutError("Unknown organization")
    return org


def infer_org(conn, payload: dict) -> str | None:
    """Which organization a ministering report belongs to, from the ministers' gender.

    LCR's PDF doesn't say. Returns None if it's not clear (no matched ministers, or a real mix).
    """
    pids = [e["person_id"] for d in payload["districts"] for g in d["groups"] for e in g["ministers"]
            if e["person_id"] is not None]
    if not pids:
        return None
    marks = ",".join("?" * len(set(pids)))
    genders = [r[0] for r in conn.execute(f"SELECT gender FROM people WHERE id IN ({marks})", list(set(pids)))]
    for org, gender in ORG_GENDER.items():
        if genders.count(gender) >= 0.8 * len(genders):
            return org
    return None


class LayoutError(ValueError):
    pass


class VersionConflict(Exception):
    pass


# --- People / directory -----------------------------------------------------

def _age(row, today: date | None = None) -> int | None:
    if not row["birth_year"] or not row["birth_month"]:
        return None
    today = today or date.today()
    return today.year - row["birth_year"] - ((today.month, today.day) < (row["birth_month"], row["birth_day"] or 1))


def sync_people(conn: sqlite3.Connection, members: list[directory.Member]) -> dict:
    """Make the active roster match the directory. Returns names moved in, moved out, and returned."""
    first_import = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 0
    existing = [dict(r) for r in conn.execute("SELECT * FROM people")]
    by_key = {(p["name"], p["birth_year"], p["birth_month"], p["birth_day"]): p for p in existing}
    by_name: dict[str, list[dict]] = {}
    for p in existing:
        by_name.setdefault(p["name"], []).append(p)

    seen: set[int] = set()
    moved_in, returned = [], []
    for m in members:
        p = by_key.get((m.name, m.birth_year, m.birth_month, m.birth_day))
        if p is None:  # same name, corrected birth date
            candidates = [c for c in by_name.get(m.name, []) if c["id"] not in seen]
            p = candidates[0] if len(candidates) == 1 else None
        if p is None:
            cur = conn.execute(
                "INSERT INTO people (name, gender, birth_year, birth_month, birth_day) VALUES (?, ?, ?, ?, ?)",
                (m.name, m.gender, m.birth_year, m.birth_month, m.birth_day))
            seen.add(cur.lastrowid)
            if not first_import:
                moved_in.append(m.name)
                conn.execute("INSERT INTO person_tags (person_id, tag) VALUES (?, 'New move-in')", (cur.lastrowid,))
            continue
        seen.add(p["id"])
        if not p["active"]:
            returned.append(m.name)
        conn.execute(
            "UPDATE people SET gender = ?, birth_year = ?, birth_month = ?, birth_day = ?, active = 1, "
            "left_at = NULL WHERE id = ?",
            (m.gender, m.birth_year, m.birth_month, m.birth_day, p["id"]))

    moved_out = [p for p in existing if p["active"] and p["id"] not in seen]
    conn.executemany("UPDATE people SET active = 0, left_at = datetime('now') WHERE id = ?",
                     [(p["id"],) for p in moved_out])
    return {"moved_in": sorted(moved_in), "moved_out": sorted(p["name"] for p in moved_out),
            "returned": sorted(returned), "total": len(members), "first_import": first_import}


def directory_birthdays(members: list[directory.Member]) -> list[Birthday]:
    return sorted({Birthday(m.birth_month, m.birth_day, m.name)
                   for m in members if m.birth_month and m.birth_day})


def people(conn: sqlite3.Connection, ids: set[int] | None = None) -> dict[int, dict]:
    """Active people plus any of `ids` (e.g. moved-out people still in a layout)."""
    rows = conn.execute("SELECT * FROM people ORDER BY name COLLATE NOCASE").fetchall()
    tags: dict[int, list[str]] = {}
    for r in conn.execute("SELECT person_id, tag FROM person_tags"):
        tags.setdefault(r["person_id"], []).append(r["tag"])
    out = {}
    for r in rows:
        if r["active"] or (ids and r["id"] in ids):
            out[r["id"]] = {
                "id": r["id"], "name": r["name"], "display": display_name(r["name"]),
                "gender": r["gender"], "age": _age(r), "active": bool(r["active"]),
                "left_at": r["left_at"], "tags": sorted(tags.get(r["id"], []), key=_tag_order)}
    return out


def _tag_order(tag: str) -> int:
    return PRIORITY_TAGS.index(tag) if tag in PRIORITY_TAGS else len(PRIORITY_TAGS)


def set_tags(conn: sqlite3.Connection, person_id: int, tags: list[str]) -> list[str]:
    if not conn.execute("SELECT 1 FROM people WHERE id = ?", (person_id,)).fetchone():
        raise LayoutError("Unknown person")
    clean = sorted({t for t in tags if t in PRIORITY_TAGS}, key=_tag_order)
    conn.execute("DELETE FROM person_tags WHERE person_id = ?", (person_id,))
    conn.executemany("INSERT INTO person_tags (person_id, tag) VALUES (?, ?)", [(person_id, t) for t in clean])
    return clean


# --- Importing the LCR ministering report -----------------------------------

def _norm(name: str) -> str:
    return " ".join(name.lower().replace(".", "").replace(",", " , ").split())


def match_names(conn: sqlite3.Connection, districts: list[report.District]) -> dict:
    """Resolve every name in the report to a person. Returns the payload for a pending import."""
    rows = [dict(r) for r in conn.execute("SELECT id, name, gender, birth_month, birth_day, active FROM people")]
    exact: dict[str, list[dict]] = {}
    normed: dict[str, list[dict]] = {}
    for r in rows:
        exact.setdefault(r["name"], []).append(r)
        normed.setdefault(_norm(r["name"]), []).append(r)

    def resolve(name: str, gender=None, month=None, day=None) -> int | None:
        for pool in (exact.get(name, []), normed.get(_norm(name), [])):
            if month:  # assigned entries carry gender + birthday: use them to break ties
                pool = [p for p in pool if p["birth_month"] == month and p["birth_day"] == day] or pool
            active = [p for p in pool if p["active"]] or pool
            if len(active) == 1:
                return active[0]["id"]
        return None

    unmatched: dict[str, dict] = {}

    def entry(name, role, **info) -> dict:
        pid = resolve(name, **info)
        if pid is None:
            unmatched.setdefault(name, {"name": name, "role": role, **info})
        return {"name": name, "person_id": pid}

    out = []
    for d in districts:
        out.append({"name": d.name, "supervisor": d.supervisor, "groups": [
            {"ministers": [entry(n, "minister") for n in c.ministers],
             "assigned": [entry(a.name, "assigned", gender=a.gender, month=a.month, day=a.day)
                          for a in c.assigned]}
            for c in d.companionships]})

    names = [r["name"] for r in rows if r["active"]]
    for u in unmatched.values():
        close = difflib.get_close_matches(u["name"], names, n=5, cutoff=0.5)
        u["suggestions"] = [{"id": exact[n][0]["id"], "name": n} for n in close]
    return {"districts": out, "unmatched": list(unmatched.values())}


def save_pending(conn, created_by: str, payload: dict) -> int:
    conn.execute("DELETE FROM pending_imports WHERE created_at < datetime('now', '-1 day')")
    return conn.execute("INSERT INTO pending_imports (created_by, payload) VALUES (?, ?)",
                        (created_by, json.dumps(payload))).lastrowid


def load_pending(conn, pending_id: int) -> dict | None:
    row = conn.execute("SELECT payload FROM pending_imports WHERE id = ?", (pending_id,)).fetchone()
    return json.loads(row["payload"]) if row else None


def finish_import(conn, payload: dict, resolutions: dict[str, int | None], created_by: str, org: str) -> int:
    """Create the imported layout. `resolutions` maps unmatched names to a person id (or None = skip)."""
    def pid(e):
        return e["person_id"] if e["person_id"] is not None else resolutions.get(e["name"])

    structure = [{"name": d["name"], "supervisor": d["supervisor"], "groups": [
        {"ministers": [p for e in g["ministers"] if (p := pid(e))],
         "assigned": [p for e in g["assigned"] if (p := pid(e))]}
        for g in d["groups"]]} for d in payload["districts"]]

    check_org(org)
    conn.execute("UPDATE layouts SET is_current = 0 WHERE kind = 'imported' AND org = ?", (org,))
    today = date.today()
    layout_id = conn.execute(
        "INSERT INTO layouts (name, org, kind, status, is_current, created_by) "
        "VALUES (?, ?, 'imported', 'approved', 1, ?)",
        (f"LCR assignments {today:%b} {today.day}, {today.year}", org, created_by)).lastrowid
    _write_structure(conn, layout_id, structure)
    return layout_id


# --- Layouts ----------------------------------------------------------------

def list_layouts(conn, org: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT l.*, (SELECT COUNT(*) FROM companionships c JOIN districts d ON d.id = c.district_id "
        "            WHERE d.layout_id = l.id) AS groups "
        "FROM layouts l WHERE l.org = ? ORDER BY l.kind = 'imported', l.updated_at DESC", (org,))]


def get_layout(conn, layout_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM layouts WHERE id = ?", (layout_id,)).fetchone()
    return dict(row) if row else None


def current_import(conn, org: str) -> dict | None:
    row = conn.execute("SELECT * FROM layouts WHERE kind = 'imported' AND is_current = 1 AND org = ?",
                       (org,)).fetchone()
    return dict(row) if row else None


def is_readonly(layout: dict) -> bool:
    return layout["kind"] == "imported" or layout["status"] == "approved"


def structure(conn, layout_id: int) -> list[dict]:
    """[{name, supervisor, groups: [{ministers: [pid], assigned: [pid]}]}] in display order."""
    districts = []
    by_id = {}
    for d in conn.execute("SELECT * FROM districts WHERE layout_id = ? ORDER BY position", (layout_id,)):
        by_id[d["id"]] = {"name": d["name"], "supervisor": d["supervisor"], "groups": []}
        districts.append(by_id[d["id"]])
    groups = {}
    for c in conn.execute(
            "SELECT c.* FROM companionships c JOIN districts d ON d.id = c.district_id "
            "WHERE d.layout_id = ? ORDER BY c.position", (layout_id,)):
        groups[c["id"]] = {"ministers": [], "assigned": []}
        by_id[c["district_id"]]["groups"].append(groups[c["id"]])
    for m in conn.execute(
            "SELECT m.* FROM companionship_members m JOIN companionships c ON c.id = m.companionship_id "
            "JOIN districts d ON d.id = c.district_id WHERE d.layout_id = ? ORDER BY m.position", (layout_id,)):
        key = "ministers" if m["role"] == "minister" else "assigned"
        groups[m["companionship_id"]][key].append(m["person_id"])
    return districts


def _write_structure(conn, layout_id: int, districts: list[dict]) -> None:
    conn.execute("DELETE FROM districts WHERE layout_id = ?", (layout_id,))
    for di, d in enumerate(districts):
        did = conn.execute("INSERT INTO districts (layout_id, name, supervisor, position) VALUES (?, ?, ?, ?)",
                           (layout_id, d["name"], d.get("supervisor", ""), di)).lastrowid
        for gi, g in enumerate(d["groups"]):
            cid = conn.execute("INSERT INTO companionships (district_id, position) VALUES (?, ?)",
                               (did, gi)).lastrowid
            for role, key in (("minister", "ministers"), ("assigned", "assigned")):
                conn.executemany(
                    "INSERT OR IGNORE INTO companionship_members (companionship_id, person_id, role, position) "
                    "VALUES (?, ?, ?, ?)", [(cid, p, role, i) for i, p in enumerate(g[key])])


def validate_structure(conn, raw) -> list[dict]:
    """Check a structure posted by the board and return a clean copy."""
    if not isinstance(raw, list) or len(raw) > 50:
        raise LayoutError("Bad layout structure")
    valid_ids = {r[0] for r in conn.execute("SELECT id FROM people")}
    clean = []
    for d in raw:
        if not isinstance(d, dict) or not isinstance(d.get("groups"), list) or len(d["groups"]) > 200:
            raise LayoutError("Bad district")
        groups = []
        for g in d["groups"]:
            if not isinstance(g, dict):
                raise LayoutError("Bad group")
            ministers, assigned = g.get("ministers", []), g.get("assigned", [])
            if not all(isinstance(p, int) and p in valid_ids for p in [*ministers, *assigned]):
                raise LayoutError("Unknown person in layout")
            groups.append({"ministers": list(dict.fromkeys(ministers)), "assigned": list(dict.fromkeys(assigned))})
        clean.append({"name": str(d.get("name") or "District").strip()[:80] or "District",
                      "supervisor": str(d.get("supervisor") or "").strip()[:80], "groups": groups})
    return clean


def save_structure(conn, layout_id: int, raw, expected_version: int) -> int:
    layout = get_layout(conn, layout_id)
    if layout is None:
        raise LayoutError("Layout not found")
    if is_readonly(layout):
        raise LayoutError("This layout is read-only.")
    clean = validate_structure(conn, raw)
    cur = conn.execute(
        "UPDATE layouts SET version = version + 1, updated_at = datetime('now') WHERE id = ? AND version = ?",
        (layout_id, expected_version))
    if cur.rowcount != 1:
        raise VersionConflict()
    _write_structure(conn, layout_id, clean)
    return expected_version + 1


def create_layout(conn, org: str, name: str, start: str, created_by: str, copy_from: int | None = None) -> int:
    """start: 'scratch' (current districts, no groups), 'current' (copy of latest import) or 'copy'."""
    check_org(org)
    source = None
    if start == "current":
        source = current_import(conn, org)
        if source is None:
            raise LayoutError(f"Import the current {ORGS[org]} assignments first.")
    elif start == "copy":
        source = get_layout(conn, copy_from) if copy_from else None
        if source is None or source["org"] != org:
            raise LayoutError("Pick a layout to copy.")
    layout_id = conn.execute("INSERT INTO layouts (name, org, kind, created_by) VALUES (?, ?, 'draft', ?)",
                             (name.strip()[:80] or "Untitled layout", org, created_by)).lastrowid
    if source:
        districts = structure(conn, source["id"])
    else:
        base = current_import(conn, org)
        districts = [{"name": d["name"], "supervisor": d["supervisor"], "groups": []}
                     for d in (structure(conn, base["id"]) if base else [])] or \
                    [{"name": "District 1", "supervisor": "", "groups": []}]
    _write_structure(conn, layout_id, districts)
    return layout_id


def update_meta(conn, layout_id: int, name: str | None = None, status: str | None = None) -> None:
    layout = get_layout(conn, layout_id)
    if layout is None or layout["kind"] != "draft":
        raise LayoutError("Only draft layouts can be changed.")
    if status is not None:
        if status not in STATUSES:
            raise LayoutError("Unknown status")
        conn.execute("UPDATE layouts SET status = ?, updated_at = datetime('now') WHERE id = ?", (status, layout_id))
    if name is not None:
        conn.execute("UPDATE layouts SET name = ?, updated_at = datetime('now') WHERE id = ?",
                     (name.strip()[:80] or layout["name"], layout_id))


def delete_layout(conn, layout_id: int) -> None:
    layout = get_layout(conn, layout_id)
    if layout is None or layout["kind"] != "draft":
        raise LayoutError("Only draft layouts can be deleted.")
    conn.execute("DELETE FROM layouts WHERE id = ?", (layout_id,))


# --- Summaries --------------------------------------------------------------

def assigned_scope(conn, org: str) -> str:
    """Who this organization usually ministers to: 'M', 'F' or 'all' (from its current import)."""
    current = current_import(conn, org)
    if current:
        ids = {p for d in structure(conn, current["id"]) for g in d["groups"] for p in g["assigned"]}
        if ids:
            marks = ",".join("?" * len(ids))
            genders = {r[0] for r in conn.execute(f"SELECT gender FROM people WHERE id IN ({marks})", list(ids))}
            return genders.pop() if len(genders) == 1 else "all"
    return ORG_GENDER[org] if org == "rs" else "all"


def org_summary(conn, org: str) -> dict:
    """Headline numbers for an organization, from what's in LCR now (its current import)."""
    current = current_import(conn, org)
    drafts = [l for l in list_layouts(conn, org) if l["kind"] == "draft"]
    out = {"key": org, "name": ORGS[org], "current": current, "drafts": drafts,
           "proposed": [l for l in drafts if l["status"] == "proposed"],
           "groups": 0, "unassigned": None, "nonminister": None}
    if current:
        groups = [g for d in structure(conn, current["id"]) for g in d["groups"]]
        ministers = {p for g in groups for p in g["ministers"]}
        assigned = {p for g in groups for p in g["assigned"]}
        scope = assigned_scope(conn, org)
        active = conn.execute("SELECT id, gender FROM people WHERE active = 1").fetchall()
        out["groups"] = len(groups)
        out["unassigned"] = sum(1 for r in active if r["id"] not in assigned and scope in ("all", r["gender"]))
        out["nonminister"] = sum(1 for r in active if r["id"] not in ministers and r["gender"] == ORG_GENDER[org])
    return out


# --- History ----------------------------------------------------------------

def history(conn, org: str, exclude_layout: int) -> dict:
    """Who were companions, and who ministered to whom, in past LCR imports.

    The current import is left out: it's the starting point, so flagging it would mark every
    unchanged group. History is about pairings from before that.
    """
    companions: dict[str, list[str]] = {}
    ministered: dict[str, list[str]] = {}
    layouts = conn.execute(
        "SELECT id, name FROM layouts WHERE id != ? AND org = ? AND kind = 'imported' AND is_current = 0 "
        "ORDER BY created_at", (exclude_layout, org)).fetchall()
    for layout in layouts:
        for d in structure(conn, layout["id"]):
            for g in d["groups"]:
                ms = sorted(g["ministers"])
                for i, a in enumerate(ms):
                    for b in ms[i + 1:]:
                        companions.setdefault(f"{a}-{b}", []).append(layout["name"])
                    for p in g["assigned"]:
                        ministered.setdefault(f"{a}-{p}", []).append(layout["name"])
    return {"companions": companions, "ministered": ministered}


# --- Changes to enter in LCR ------------------------------------------------

def _flatten(districts: list[dict]) -> list[dict]:
    return [{"district": d["name"], "ministers": set(g["ministers"]), "assigned": set(g["assigned"]),
             "order": (di, gi)}
            for di, d in enumerate(districts) for gi, g in enumerate(d["groups"])]


def changes(base: list[dict], target: list[dict]) -> dict:
    """What to change in LCR to get from `base` (current import) to `target`.

    Companionships are matched by overlap: shared ministers count most, shared assignments break ties.
    """
    old, new = _flatten(base), _flatten(target)
    scored = sorted(
        ((3 * len(o["ministers"] & n["ministers"]) + len(o["assigned"] & n["assigned"]), oi, ni)
         for oi, o in enumerate(old) for ni, n in enumerate(new)), reverse=True)
    pairs, used_old, used_new = [], set(), set()
    for score, oi, ni in scored:
        if score < 2 or oi in used_old or ni in used_new:
            continue
        pairs.append((oi, ni))
        used_old.add(oi)
        used_new.add(ni)

    was_assigned_to = {p: o for o in old for p in o["assigned"]}
    result = {"removed": [], "changed": [], "created": []}
    for oi, o in enumerate(old):
        if oi not in used_old:
            result["removed"].append({"district": o["district"], "ministers": o["ministers"],
                                      "assigned": o["assigned"]})
    for oi, ni in sorted(pairs, key=lambda p: new[p[1]]["order"]):
        o, n = old[oi], new[ni]
        item = {"ministers": o["ministers"], "district": o["district"],
                "new_district": n["district"] if n["district"] != o["district"] else None,
                "add_ministers": n["ministers"] - o["ministers"],
                "remove_ministers": o["ministers"] - n["ministers"],
                "add_assigned": n["assigned"] - o["assigned"],
                "remove_assigned": o["assigned"] - n["assigned"]}
        if any(item[k] for k in ("new_district", "add_ministers", "remove_ministers", "add_assigned",
                                 "remove_assigned")):
            result["changed"].append(item)
    for ni, n in enumerate(new):
        if ni not in used_new and (n["ministers"] or n["assigned"]):
            result["created"].append({"district": n["district"], "ministers": n["ministers"],
                                      "assigned": n["assigned"]})
    old_districts = {d["name"] for d in base}
    result["new_districts"] = [d for d in target if d["name"] not in old_districts and d["groups"]]
    result["was_assigned_to"] = was_assigned_to
    return result

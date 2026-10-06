"""Ministering pages and the JSON API used by the drag-and-drop board. Leaders and admins only."""
import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import auth, db, ministering as m
from .web import flash, render

router = APIRouter()
leader = Depends(auth.require_leader)


def _layout_or_404(conn, layout_id: int) -> dict:
    layout = m.get_layout(conn, layout_id)
    if layout is None:
        raise HTTPException(status_code=404, detail="Layout not found")
    return layout


def board_state(conn, layout: dict) -> dict:
    org = layout["org"]
    districts = m.structure(conn, layout["id"])
    in_layout = {p for d in districts for g in d["groups"] for p in g["ministers"] + g["assigned"]}
    current = m.current_import(conn, org)
    return {
        "layout": {k: layout[k] for k in ("id", "name", "kind", "status", "version", "updated_at", "verified_at")}
                  | {"readonly": m.is_readonly(layout),
                     "is_current": bool(layout["is_current"])},
        "org": org,
        "org_name": m.ORGS[org],
        "minister_gender": m.ORG_GENDER[org],
        "assigned_scope": m.assigned_scope(conn, org),
        "districts": districts,
        "people": m.people(conn, in_layout),
        "history": m.history(conn, org, layout["id"]),
        "tags": m.PRIORITY_TAGS,
        "needs_ministers_tags": sorted(m.NEEDS_MINISTERS_TAGS),
        "current_import_id": current["id"] if current else None,
    }


async def _json(request: Request) -> dict:
    # Requiring a JSON content type means plain cross-site forms can't hit these endpoints.
    if not request.headers.get("content-type", "").startswith("application/json"):
        raise HTTPException(status_code=415, detail="Expected JSON")
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Bad JSON")
    return body


# --- Pages ------------------------------------------------------------------
# Fixed paths are registered before /ministering/{org} so "people", "layouts" etc. match first.

def _org_or_404(org: str) -> str:
    if org not in m.ORGS:
        raise HTTPException(status_code=404, detail="Not found")
    return org


@router.get("/ministering")
def landing(user: dict = leader):
    # The dashboard's Elders Quorum and Relief Society tiles replaced this page.
    return RedirectResponse("/", status_code=303)


@router.post("/ministering/layouts/{layout_id}/delete")
def delete_layout(layout_id: int, request: Request, user: dict = leader):
    org = "eq"
    try:
        with db.ward_connect(user["ward"]["id"]) as conn:
            org = _layout_or_404(conn, layout_id)["org"]
            m.delete_layout(conn, layout_id)
        flash(request, "ok", "Layout deleted.")
    except m.LayoutError as exc:
        flash(request, "error", str(exc))
    return RedirectResponse(f"/ministering/{org}", status_code=303)


@router.get("/ministering/layouts/{layout_id}", response_class=HTMLResponse)
def board(layout_id: int, request: Request, user: dict = leader):
    with db.ward_connect(user["ward"]["id"]) as conn:
        layout = _layout_or_404(conn, layout_id)
        state = board_state(conn, layout)
    return render(request, "ministering/board.html", user=user, layout=layout, state=state,
                  statuses=m.STATUSES, org_name=m.ORGS[layout["org"]], wide=True)


@router.get("/ministering/layouts/{layout_id}/changes", response_class=HTMLResponse)
def changes(layout_id: int, request: Request, user: dict = leader):
    with db.ward_connect(user["ward"]["id"]) as conn:
        layout = _layout_or_404(conn, layout_id)
        org_name = m.ORGS[layout["org"]]
        current = m.current_import(conn, layout["org"])
        if current is None:
            flash(request, "error", f"Import the current {org_name} assignments first.")
            return RedirectResponse(f"/ministering/layouts/{layout_id}", status_code=303)
        diff = m.changes(m.structure(conn, current["id"]), m.structure(conn, layout_id))
        ids = {p for k in ("removed", "changed", "created") for item in diff[k]
               for key in ("ministers", "assigned", "add_ministers", "remove_ministers",
                           "add_assigned", "remove_assigned") for p in item.get(key, ())}
        people = m.people(conn, ids)
        check = m.lcr_check(conn, layout)
    return render(request, "ministering/changes.html", user=user, layout=layout, current=current,
                  diff=diff, people=people, org_name=org_name, check=check)


@router.get("/ministering/people", response_class=HTMLResponse)
def people_page(request: Request, user: dict = leader):
    with db.ward_connect(user["ward"]["id"]) as conn:
        all_ids = {r[0] for r in conn.execute("SELECT id FROM people")}
        people = m.people(conn, all_ids)
        roles: dict[int, set] = {}   # pid -> {"eq:minister", "rs:assigned", ...}
        imported = []
        for org in m.ORGS:
            current = m.current_import(conn, org)
            if not current:
                continue
            imported.append(org)
            for d in m.structure(conn, current["id"]):
                for g in d["groups"]:
                    for p in g["ministers"]:
                        roles.setdefault(p, set()).add(f"{org}:minister")
                    for p in g["assigned"]:
                        roles.setdefault(p, set()).add(f"{org}:assigned")
    return render(request, "ministering/people.html", user=user, people=list(people.values()),
                  roles=roles, tags=m.PRIORITY_TAGS, imported=imported, orgs=m.ORGS)


@router.get("/ministering/import/{pending_id}", response_class=HTMLResponse)
def import_review(pending_id: int, request: Request, user: dict = leader):
    with db.ward_connect(user["ward"]["id"]) as conn:
        payload = m.load_pending(conn, pending_id)
        everyone = m.people(conn)
    if payload is None:
        flash(request, "error", "That import has expired. Upload the report again.")
        return RedirectResponse("/ministering", status_code=303)
    return render(request, "ministering/import_review.html", user=user, pending_id=pending_id,
                  unmatched=payload["unmatched"], everyone=list(everyone.values()),
                  org=payload.get("org"), orgs=m.ORGS)


@router.post("/ministering/import/{pending_id}")
async def import_confirm(pending_id: int, request: Request, user: dict = leader):
    form = await request.form()
    org = form.get("org", "")
    if org not in m.ORGS:
        flash(request, "error", "Pick whether this is the Elders Quorum or Relief Society report.")
        return RedirectResponse(f"/ministering/import/{pending_id}", status_code=303)
    with db.ward_connect(user["ward"]["id"]) as conn:
        payload = m.load_pending(conn, pending_id)
        if payload is None:
            flash(request, "error", "That import has expired. Upload the report again.")
            return RedirectResponse("/ministering", status_code=303)
        valid = {r[0] for r in conn.execute("SELECT id FROM people")}
        resolutions = {}
        for i, u in enumerate(payload["unmatched"]):
            choice = form.get(f"match_{i}", "")
            resolutions[u["name"]] = int(choice) if choice.isdigit() and int(choice) in valid else None
        layout_id = m.finish_import(conn, payload, resolutions, user["username"], org)
        checked = m.check_approved(conn, org, layout_id)
        conn.execute("DELETE FROM pending_imports WHERE id = ?", (pending_id,))
        conn.execute("INSERT INTO uploads (report, uploaded_by, summary) VALUES ('ministering', ?, ?)",
                     (user["username"], json.dumps({"layout_id": layout_id, "org": org})))
    skipped = sum(v is None for v in resolutions.values())
    flash(request, "ok", f"Imported the current {m.ORGS[org]} assignments."
          + (f" {skipped} unmatched name(s) were skipped." if skipped else "")
          + (" " + m.check_note(checked) if checked else ""))
    return RedirectResponse(after_import(layout_id, checked), status_code=303)


def after_import(import_id: int, checked: list[dict]) -> str:
    """Where to go after an import: the checked layout's change list if it was checking one."""
    return f"/ministering/layouts/{checked[0]['id']}/changes" if checked else f"/ministering/layouts/{import_id}"


@router.post("/ministering/layouts/{layout_id}/verify")
def verify_layout(layout_id: int, request: Request, user: dict = leader):
    try:
        with db.ward_connect(user["ward"]["id"]) as conn:
            _layout_or_404(conn, layout_id)
            m.mark_verified(conn, layout_id)
        flash(request, "ok", "Marked as done in LCR.")
    except m.LayoutError as exc:
        flash(request, "error", str(exc))
    return RedirectResponse(f"/ministering/layouts/{layout_id}/changes", status_code=303)


@router.get("/ministering/{org}", response_class=HTMLResponse)
def org_index(org: str, request: Request, user: dict = leader):
    _org_or_404(org)
    with db.ward_connect(user["ward"]["id"]) as conn:
        layouts = m.list_layouts(conn, org)
        summary = m.org_summary(conn, org)
    return render(request, "ministering/index.html", user=user, layouts=layouts, current=summary["current"],
                  summary=summary, org=org, org_name=m.ORGS[org])


@router.post("/ministering/{org}/layouts")
def create_layout(org: str, request: Request, name: str = Form(""), start: str = Form("current"),
                  copy_from: int | None = Form(None), user: dict = leader):
    _org_or_404(org)
    try:
        with db.ward_connect(user["ward"]["id"]) as conn:
            layout_id = m.create_layout(conn, org, name, start, user["username"], copy_from)
    except m.LayoutError as exc:
        flash(request, "error", str(exc))
        return RedirectResponse(f"/ministering/{org}", status_code=303)
    return RedirectResponse(f"/ministering/layouts/{layout_id}", status_code=303)


# --- JSON API for the board -------------------------------------------------

@router.get("/api/ministering/layouts/{layout_id}")
def api_state(layout_id: int, user: dict = leader):
    with db.ward_connect(user["ward"]["id"]) as conn:
        return board_state(conn, _layout_or_404(conn, layout_id))


@router.put("/api/ministering/layouts/{layout_id}/structure")
async def api_save(layout_id: int, request: Request, user: dict = leader):
    body = await _json(request)
    try:
        with db.ward_connect(user["ward"]["id"]) as conn:
            version = m.save_structure(conn, layout_id, body.get("districts"), int(body.get("version", 0)))
    except m.VersionConflict:
        return JSONResponse({"error": "conflict"}, status_code=409)
    except (m.LayoutError, TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"version": version}


@router.patch("/api/ministering/layouts/{layout_id}")
async def api_meta(layout_id: int, request: Request, user: dict = leader):
    body = await _json(request)
    try:
        with db.ward_connect(user["ward"]["id"]) as conn:
            m.update_meta(conn, layout_id, name=body.get("name"), status=body.get("status"))
            layout = _layout_or_404(conn, layout_id)
    except m.LayoutError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"name": layout["name"], "status": layout["status"], "readonly": m.is_readonly(layout),
            "verified": bool(layout["verified_at"])}


@router.put("/api/ministering/people/{person_id}/tags")
async def api_tags(person_id: int, request: Request, user: dict = leader):
    body = await _json(request)
    tags = body.get("tags")
    if not isinstance(tags, list):
        return JSONResponse({"error": "tags must be a list"}, status_code=400)
    try:
        with db.ward_connect(user["ward"]["id"]) as conn:
            return {"tags": m.set_tags(conn, person_id, [str(t) for t in tags])}
    except m.LayoutError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)

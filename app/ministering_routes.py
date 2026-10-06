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
    districts = m.structure(conn, layout["id"])
    in_layout = {p for d in districts for g in d["groups"] for p in g["ministers"] + g["assigned"]}
    current = m.current_import(conn)
    return {
        "layout": {k: layout[k] for k in ("id", "name", "kind", "status", "version", "updated_at")}
                  | {"readonly": m.is_readonly(layout),
                     "is_current": bool(layout["is_current"])},
        "districts": districts,
        "people": m.people(conn, in_layout),
        "history": m.history(conn, layout["id"]),
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

@router.get("/ministering", response_class=HTMLResponse)
def index(request: Request, user: dict = leader):
    with db.connect() as conn:
        layouts = m.list_layouts(conn)
        current = m.current_import(conn)
        stats = conn.execute(
            "SELECT SUM(active) AS active, SUM(1 - active) AS moved_out FROM people").fetchone()
    return render(request, "ministering/index.html", user=user, layouts=layouts, current=current,
                  stats=dict(stats), statuses=m.STATUSES)


@router.post("/ministering/layouts")
def create_layout(request: Request, name: str = Form(""), start: str = Form("current"),
                  copy_from: int | None = Form(None), user: dict = leader):
    try:
        with db.connect() as conn:
            layout_id = m.create_layout(conn, name, start, user["username"], copy_from)
    except m.LayoutError as exc:
        flash(request, "error", str(exc))
        return RedirectResponse("/ministering", status_code=303)
    return RedirectResponse(f"/ministering/layouts/{layout_id}", status_code=303)


@router.post("/ministering/layouts/{layout_id}/delete")
def delete_layout(layout_id: int, request: Request, user: dict = leader):
    try:
        with db.connect() as conn:
            m.delete_layout(conn, layout_id)
        flash(request, "ok", "Layout deleted.")
    except m.LayoutError as exc:
        flash(request, "error", str(exc))
    return RedirectResponse("/ministering", status_code=303)


@router.get("/ministering/layouts/{layout_id}", response_class=HTMLResponse)
def board(layout_id: int, request: Request, user: dict = leader):
    with db.connect() as conn:
        layout = _layout_or_404(conn, layout_id)
        state = board_state(conn, layout)
    return render(request, "ministering/board.html", user=user, layout=layout, state=state,
                  statuses=m.STATUSES, wide=True)


@router.get("/ministering/layouts/{layout_id}/changes", response_class=HTMLResponse)
def changes(layout_id: int, request: Request, user: dict = leader):
    with db.connect() as conn:
        layout = _layout_or_404(conn, layout_id)
        current = m.current_import(conn)
        if current is None:
            flash(request, "error", "Import the current ministering assignments first.")
            return RedirectResponse(f"/ministering/layouts/{layout_id}", status_code=303)
        diff = m.changes(m.structure(conn, current["id"]), m.structure(conn, layout_id))
        ids = {p for k in ("removed", "changed", "created") for item in diff[k]
               for key in ("ministers", "assigned", "add_ministers", "remove_ministers",
                           "add_assigned", "remove_assigned") for p in item.get(key, ())}
        people = m.people(conn, ids)
    return render(request, "ministering/changes.html", user=user, layout=layout, current=current,
                  diff=diff, people=people)


@router.get("/ministering/people", response_class=HTMLResponse)
def people_page(request: Request, user: dict = leader):
    with db.connect() as conn:
        all_ids = {r[0] for r in conn.execute("SELECT id FROM people")}
        people = m.people(conn, all_ids)
        current = m.current_import(conn)
        roles: dict[int, set] = {}
        if current:
            for d in m.structure(conn, current["id"]):
                for g in d["groups"]:
                    for p in g["ministers"]:
                        roles.setdefault(p, set()).add("minister")
                    for p in g["assigned"]:
                        roles.setdefault(p, set()).add("assigned")
    return render(request, "ministering/people.html", user=user, people=list(people.values()),
                  roles=roles, tags=m.PRIORITY_TAGS, has_import=current is not None)


@router.get("/ministering/import/{pending_id}", response_class=HTMLResponse)
def import_review(pending_id: int, request: Request, user: dict = leader):
    with db.connect() as conn:
        payload = m.load_pending(conn, pending_id)
        everyone = m.people(conn)
    if payload is None:
        flash(request, "error", "That import has expired. Upload the report again.")
        return RedirectResponse("/ministering", status_code=303)
    return render(request, "ministering/import_review.html", user=user, pending_id=pending_id,
                  unmatched=payload["unmatched"], everyone=list(everyone.values()))


@router.post("/ministering/import/{pending_id}")
async def import_confirm(pending_id: int, request: Request, user: dict = leader):
    form = await request.form()
    with db.connect() as conn:
        payload = m.load_pending(conn, pending_id)
        if payload is None:
            flash(request, "error", "That import has expired. Upload the report again.")
            return RedirectResponse("/ministering", status_code=303)
        valid = {r[0] for r in conn.execute("SELECT id FROM people")}
        resolutions = {}
        for i, u in enumerate(payload["unmatched"]):
            choice = form.get(f"match_{i}", "")
            resolutions[u["name"]] = int(choice) if choice.isdigit() and int(choice) in valid else None
        layout_id = m.finish_import(conn, payload, resolutions, user["username"])
        conn.execute("DELETE FROM pending_imports WHERE id = ?", (pending_id,))
        conn.execute("INSERT INTO uploads (report, uploaded_by, summary) VALUES ('ministering', ?, ?)",
                     (user["username"], json.dumps({"layout_id": layout_id})))
    skipped = sum(v is None for v in resolutions.values())
    flash(request, "ok", "Imported the current ministering assignments."
          + (f" {skipped} unmatched name(s) were skipped." if skipped else ""))
    return RedirectResponse(f"/ministering/layouts/{layout_id}", status_code=303)


# --- JSON API for the board -------------------------------------------------

@router.get("/api/ministering/layouts/{layout_id}")
def api_state(layout_id: int, user: dict = leader):
    with db.connect() as conn:
        return board_state(conn, _layout_or_404(conn, layout_id))


@router.put("/api/ministering/layouts/{layout_id}/structure")
async def api_save(layout_id: int, request: Request, user: dict = leader):
    body = await _json(request)
    try:
        with db.connect() as conn:
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
        with db.connect() as conn:
            m.update_meta(conn, layout_id, name=body.get("name"), status=body.get("status"))
            layout = _layout_or_404(conn, layout_id)
    except m.LayoutError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"name": layout["name"], "status": layout["status"], "readonly": m.is_readonly(layout)}


@router.put("/api/ministering/people/{person_id}/tags")
async def api_tags(person_id: int, request: Request, user: dict = leader):
    body = await _json(request)
    tags = body.get("tags")
    if not isinstance(tags, list):
        return JSONResponse({"error": "tags must be a list"}, status_code=400)
    try:
        with db.connect() as conn:
            return {"tags": m.set_tags(conn, person_id, [str(t) for t in tags])}
    except m.LayoutError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)

"""Move-in welcome checklist page. Leaders and admins only."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import auth, db, welcome
from .web import flash, render

router = APIRouter()
leader = Depends(auth.require_leader)


@router.get("/welcome", response_class=HTMLResponse)
def page(request: Request, user: dict = leader):
    with db.ward_connect(user["ward"]["id"]) as conn:
        rows = welcome.checklist(conn)
    return render(request, "welcome.html", user=user, rows=rows, steps=welcome.STEPS)


@router.post("/welcome/{person_id}/step")
def step(person_id: int, request: Request, step: str = Form(...), done: str = Form("1"), user: dict = leader):
    try:
        with db.ward_connect(user["ward"]["id"]) as conn:
            welcome.set_step(conn, person_id, step, done == "1")
    except welcome.WelcomeError as exc:
        flash(request, "error", str(exc))
    return RedirectResponse(f"/welcome#p{person_id}", status_code=303)


@router.post("/welcome/{person_id}/dismiss")
def dismiss(person_id: int, request: Request, user: dict = leader):
    try:
        with db.ward_connect(user["ward"]["id"]) as conn:
            welcome.dismiss(conn, person_id)
        flash(request, "ok", "Taken off the list.")
    except welcome.WelcomeError as exc:
        flash(request, "error", str(exc))
    return RedirectResponse("/welcome", status_code=303)

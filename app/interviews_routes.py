"""Ministering interview pages. Leaders and admins only."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import auth, db, interviews as iv, ministering as m
from .web import flash, render

router = APIRouter()
leader = Depends(auth.require_leader)


def _ints(values) -> list[int]:
    return [int(v) for v in values if str(v).isdigit()]


@router.get("/ministering/{org}/interviews", response_class=HTMLResponse)
def tracker(org: str, request: Request, user: dict = leader):
    if org not in m.ORGS:
        raise HTTPException(status_code=404, detail="Not found")
    with db.ward_connect(user["ward"]["id"]) as conn:
        status = iv.groups(conn, org, date.today())
        recent = iv.recent(conn, org)
    return render(request, "ministering/interviews.html", user=user, org=org, org_name=m.ORGS[org], orgs=m.ORGS,
                  status=status, recent=recent, remind_days=iv.REMIND_DAYS)


def _form(request: Request, user: dict, org: str, interview: dict | None, ministers: list[dict],
          error: str | None = None, values: dict | None = None, status_code: int = 200):
    with db.ward_connect(user["ward"]["id"]) as conn:
        ids = [p["id"] for p in ministers]
        past = iv.history(conn, ids, exclude=interview["id"] if interview else None)
        flock = iv.ministering_to(conn, org, ids)
    return render(request, "ministering/interview.html", status_code=status_code, user=user, org=org,
                  org_name=m.ORGS[org], interview=interview, ministers=ministers, past=past, flock=flock, error=error,
                  values=values or {}, today=date.today(), max_text=iv.MAX_TEXT)


@router.get("/interviews/new", response_class=HTMLResponse)
def new(request: Request, org: str = "", user: dict = leader):
    ids = _ints(request.query_params.getlist("m"))
    with db.ward_connect(user["ward"]["id"]) as conn:
        people = m.people(conn, set(ids))
        ministers = [people[i] for i in dict.fromkeys(ids) if i in people]
    if org not in m.ORGS or not ministers:
        return RedirectResponse(f"/ministering/{org if org in m.ORGS else 'eq'}/interviews", status_code=303)
    return _form(request, user, org, None, ministers)


@router.post("/interviews/new")
async def create(request: Request, user: dict = leader):
    form = await request.form()
    org = str(form.get("org", ""))
    ids = _ints(form.getlist("m"))
    if org not in m.ORGS:
        raise HTTPException(status_code=400, detail="Unknown organization")
    values = {"held_on": str(form.get("held_on", "")), "notes": str(form.get("notes", "")),
              "follow_up": str(form.get("follow_up", ""))}
    try:
        with db.ward_connect(user["ward"]["id"]) as conn:
            interview_id = iv.create(conn, org, values["held_on"], ids, values["notes"], values["follow_up"],
                                     user["username"])
    except iv.InterviewError as exc:
        with db.ward_connect(user["ward"]["id"]) as conn:
            people = m.people(conn, set(ids))
        return _form(request, user, org, None, [people[i] for i in dict.fromkeys(ids) if i in people],
                     error=str(exc), values=values, status_code=400)
    flash(request, "ok", "Saved. You can keep adding to it.")
    return RedirectResponse(f"/interviews/{interview_id}", status_code=303)


def _or_404(conn, interview_id: int) -> dict:
    interview = iv.get(conn, interview_id)
    if interview is None:
        raise HTTPException(status_code=404, detail="Interview not found")
    return interview


@router.get("/interviews/{interview_id}", response_class=HTMLResponse)
def edit(interview_id: int, request: Request, user: dict = leader):
    with db.ward_connect(user["ward"]["id"]) as conn:
        interview = _or_404(conn, interview_id)
    return _form(request, user, interview["org"], interview, interview["ministers"])


@router.post("/interviews/{interview_id}")
async def save(interview_id: int, request: Request, user: dict = leader):
    form = await request.form()
    values = {"held_on": str(form.get("held_on", "")), "notes": str(form.get("notes", "")),
              "follow_up": str(form.get("follow_up", ""))}
    with db.ward_connect(user["ward"]["id"]) as conn:
        interview = _or_404(conn, interview_id)
    try:
        with db.ward_connect(user["ward"]["id"]) as conn:
            iv.update(conn, interview_id, values["held_on"], _ints(form.getlist("m")), values["notes"],
                      values["follow_up"], user["username"])
    except iv.InterviewError as exc:
        return _form(request, user, interview["org"], interview, interview["ministers"], error=str(exc),
                     values=values, status_code=400)
    flash(request, "ok", "Saved.")
    return RedirectResponse(f"/interviews/{interview_id}", status_code=303)


@router.post("/interviews/{interview_id}/delete")
def delete(interview_id: int, request: Request, user: dict = leader):
    with db.ward_connect(user["ward"]["id"]) as conn:
        org = _or_404(conn, interview_id)["org"]
        iv.delete(conn, interview_id)
    flash(request, "ok", "Interview deleted.")
    return RedirectResponse(f"/ministering/{org}/interviews", status_code=303)

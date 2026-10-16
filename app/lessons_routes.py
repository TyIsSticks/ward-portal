"""Lesson planning pages: conference talks, the planning board and teachers. Leaders and admins only."""
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import auth, db, lessons as les
from .reports import conference as gc
from .reports.birthdays import ReportError
from .web import flash, render

router = APIRouter()
leader = Depends(auth.require_leader)


def talks_url(year: int, month: int) -> str:
    return f"/lessons/talks?conf={year}-{month:02d}"


def save_conference(request: Request, user: dict, conf: gc.Conference, source: str) -> RedirectResponse:
    """Store fetched or uploaded talks and show them for review."""
    with db.ward_connect(user["ward"]["id"]) as conn:
        if source == "pdf":
            gc.guess_required(conf.talks, les.known_required(conn))
        les.store_conference(conn, conf, source, user["username"])
        speakers = les.required_speakers(les.talks(conn, les.get_conference(conn, conf.year, conf.month)["id"]))
    how = "from the Church website" if source == "website" else "from the PDF"
    check = ("Callings came from the website." if source == "website"
             else "A PDF doesn't list callings, so check the ticked speakers below.")
    flash(request, "ok", f"Loaded {len(conf.talks)} talks for {conf.name} {how}. "
                         f"{len(speakers)} speakers are First Presidency or Twelve. {check}")
    return RedirectResponse(talks_url(conf.year, conf.month), status_code=303)


@router.get("/lessons")
def landing(conf: str = "", user: dict = leader):
    return RedirectResponse(talks_url(*les.parse_conf(conf)), status_code=303)


@router.get("/lessons/talks", response_class=HTMLResponse)
def talks_page(request: Request, conf: str = "", user: dict = leader):
    year, month = les.parse_conf(conf)
    with db.ward_connect(user["ward"]["id"]) as conn:
        record = les.get_conference(conn, year, month)
        rows = les.talks(conn, record["id"]) if record else []
        loaded = les.list_conferences(conn)
    sessions: dict[str, list] = {}
    for t in rows:
        sessions.setdefault(t["session"] or "Talks", []).append(t)
    return render(request, "lessons/talks.html", user=user, year=year, month=month,
                  conf_name=les.conference_name(year, month), record=record, sessions=sessions,
                  required=les.required_speakers(rows), loaded=loaded,
                  prev=les.prev_conference(year, month), next=les.next_conference(year, month),
                  current=les.conference_for(date.today()))


@router.post("/lessons/talks/fetch")
def fetch_talks(request: Request, conf: str = Form(""), user: dict = leader):
    year, month = les.parse_conf(conf)
    try:
        fetched = gc.fetch(year, month)
    except ReportError as exc:
        flash(request, "error", str(exc))
        return RedirectResponse(talks_url(year, month), status_code=303)
    return save_conference(request, user, fetched, "website")


@router.post("/lessons/talks/required")
async def save_required(request: Request, user: dict = leader):
    form = await request.form()
    year, month = les.parse_conf(str(form.get("conf", "")))
    with db.ward_connect(user["ward"]["id"]) as conn:
        record = les.get_conference(conn, year, month)
        if record:
            les.set_required(conn, record["id"], {str(s) for s in form.getlist("speaker")})
    flash(request, "ok", "Saved which speakers are First Presidency or Twelve.")
    return RedirectResponse(talks_url(year, month), status_code=303)

"""Lesson planning: conference talks, the planning board, teachers and calendars.

Leaders and admins only, except the calendar feeds, which (like the birthday feed) are reached
by a private link.
"""
import secrets
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from . import auth, config, db, groupme, lessons as les, wards
from .ministering_routes import _json as json_body
from .reports import conference as gc
from .reports.birthdays import ReportError
from .web import absolute_url, flash, render

router = APIRouter()
leader = Depends(auth.require_leader)
viewer = Depends(auth.require_lesson_viewer)   # leaders, plus teachers (read-only)


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


def plan_url(plan: str, year: int, month: int) -> str:
    return f"/lessons/{plan}?conf={year}-{month:02d}"


@router.get("/lessons")
def landing(conf: str = "", user: dict = viewer):
    year, month = les.parse_conf(conf)
    with db.ward_connect(user["ward"]["id"]) as conn:
        plan = les.plan_for(conn, "eq")
        # Right after conference its talks may not be loaded yet; keep planning from the one before.
        if not conf and les.is_overlap(date.today()) and les.get_conference(conn, year, month) is None:
            prev = les.prev_conference(year, month)
            if les.get_conference(conn, *prev):
                year, month = prev
    return RedirectResponse(plan_url(plan, year, month), status_code=303)


@router.post("/lessons/settings")
def save_settings(request: Request, shared: str = Form(""), conf: str = Form(""), user: dict = leader):
    with db.ward_connect(user["ward"]["id"]) as conn:
        les.set_shared(conn, shared == "1")
    flash(request, "ok", "Elders Quorum and Relief Society now share one lesson plan." if shared == "1"
          else "Elders Quorum and Relief Society now each have their own lesson plan.")
    year, month = les.parse_conf(conf)
    return RedirectResponse(f"/lessons?conf={year}-{month:02d}", status_code=303)


@router.get("/lessons/talks", response_class=HTMLResponse)
def talks_page(request: Request, conf: str = "", user: dict = viewer):
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


# --- Teachers -----------------------------------------------------------------

@router.get("/lessons/teachers", response_class=HTMLResponse)
def teachers_page(request: Request, user: dict = leader):
    with db.ward_connect(user["ward"]["id"]) as conn:
        teachers = {o: les.list_teachers(conn, o) for o in les.ORGS}
        roster = {o: les.roster(conn, o) for o in les.ORGS}
        shared = les.is_shared(conn)
    return render(request, "lessons/teachers.html", user=user, teachers=teachers, roster=roster,
                  org_names=les.ORGS, shared=shared, today=date.today().isoformat())


@router.post("/lessons/teachers")
def add_teacher_form(request: Request, org: str = Form(...), person_id: str = Form(""), name: str = Form(""),
                     user: dict = leader):
    try:
        with db.ward_connect(user["ward"]["id"]) as conn:
            t = les.add_teacher(conn, org, name, int(person_id) if person_id.isdigit() else None)
        flash(request, "ok", f"Added {t['name']} to the {les.ORGS[org]} teachers.")
    except les.LessonError as exc:
        flash(request, "error", str(exc))
    return RedirectResponse(f"/lessons/teachers#{org}", status_code=303)


@router.post("/lessons/teachers/{teacher_id}/active")
def teacher_active(teacher_id: int, request: Request, active: str = Form(""), user: dict = leader):
    with db.ward_connect(user["ward"]["id"]) as conn:
        t = _teacher_or_404(conn, teacher_id)
        les.set_teacher_active(conn, teacher_id, active == "1")
    flash(request, "ok", f"{t['name']} is {'back on the list' if active == '1' else 'off the list for now'}.")
    return RedirectResponse(f"/lessons/teachers#{t['org']}", status_code=303)


@router.post("/lessons/teachers/{teacher_id}/remove")
def teacher_remove(teacher_id: int, request: Request, user: dict = leader):
    with db.ward_connect(user["ward"]["id"]) as conn:
        t = _teacher_or_404(conn, teacher_id)
        how = les.remove_teacher(conn, teacher_id)
    flash(request, "ok", f"Removed {t['name']}." if how == "deleted" else
          f"Removed {t['name']} from upcoming lessons. Past lessons still show their name.")
    return RedirectResponse(f"/lessons/teachers#{t['org']}", status_code=303)


def _teacher_or_404(conn, teacher_id: int) -> dict:
    t = les.get_teacher(conn, teacher_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Teacher not found")
    return t


@router.post("/api/lessons/teachers")
async def api_add_teacher(request: Request, user: dict = leader):
    body = await json_body(request)
    person_id = body.get("person_id")
    try:
        with db.ward_connect(user["ward"]["id"]) as conn:
            return les.add_teacher(conn, str(body.get("org", "")), str(body.get("name") or ""),
                                   int(person_id) if person_id else None)
    except (les.LessonError, TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


# --- Calendars ----------------------------------------------------------------

def org_feed_url(request: Request, ward_id: int, token: str, org: str) -> str:
    return absolute_url(request, f"/feed/{ward_id}/lessons/{token}/{org}.ics")


def teacher_feed_url(request: Request, ward_id: int, token: str) -> str:
    return absolute_url(request, f"/feed/{ward_id}/teacher/{token}.ics")


@router.get("/lessons/calendar", response_class=HTMLResponse)
def calendar_page(request: Request, user: dict = viewer):
    ward_id = user["ward"]["id"]
    with db.ward_connect(ward_id) as conn:
        token = les.feed_token(conn)
        shared = les.is_shared(conn)
    feeds = {o: org_feed_url(request, ward_id, token, o) for o in les.ORGS}
    return render(request, "lessons/calendar.html", user=user, feeds=feeds, org_names=les.ORGS, shared=shared)


@router.get("/lessons/groupme", response_class=HTMLResponse)
def groupme_page(request: Request, user: dict = leader):
    today = groupme.now().date()
    with db.ward_connect(user["ward"]["id"]) as conn:
        bots = {o: _lesson_bot(conn, o, today) for o in les.ORGS}
        shared = les.is_shared(conn)
    return render(request, "lessons/groupme.html", user=user, bots=bots, org_names=les.ORGS, shared=shared,
                  sunday=groupme.coming_sunday(today), days=groupme.DAYS, timezone=config.TIMEZONE,
                  times=[(t, groupme.time_label(t)) for t in groupme.TIMES],
                  voices={k: v["name"] for k, v in groupme.VOICES.items()})


def _lesson_bot(conn, org: str, today: date) -> dict:
    """A lesson bot's settings card: its voice, the ward's own lines, and a sample in every voice."""
    key = f"lessons-{org}"
    sunday = groupme.coming_sunday(today)
    own = groupme.custom_voice(conn, key)
    # Start write-your-own from the friendly lines, so there's something to edit.
    starter = {p: own[p] or "\n".join(groupme.VOICES["plain"][p]) for p in own}
    samples = {v: groupme.lesson_message(conn, org, sunday, words=groupme.VOICES[v]) for v in groupme.VOICES}
    if any(own.values()):
        samples["custom"] = groupme.lesson_message(conn, org, sunday, words=groupme.own_voice(conn, key))
    day, at = groupme.schedule(conn, key)
    return groupme.status(conn, key) | {"voice": groupme.voice(conn, key), "own": starter, "samples": samples,
                                        "day": day, "time": at, "when": groupme.schedule_label(conn, key)}


@router.post("/lessons/calendar/regenerate")
def regenerate_calendar(request: Request, user: dict = leader):
    with db.ward_connect(user["ward"]["id"]) as conn:
        les.new_feed_token(conn)
    flash(request, "ok", "New lesson calendar links created. Everyone needs to subscribe again with the new link.")
    return RedirectResponse("/lessons/calendar", status_code=303)


@router.get("/lessons/teachers/{teacher_id}/calendar", response_class=HTMLResponse)
def teacher_calendar_page(teacher_id: int, request: Request, user: dict = leader):
    with db.ward_connect(user["ward"]["id"]) as conn:
        t = _teacher_or_404(conn, teacher_id)
    return render(request, "lessons/teacher_calendar.html", user=user, teacher=t, org_name=les.ORGS[t["org"]],
                  feed_url=teacher_feed_url(request, user["ward"]["id"], t["feed_token"]))


@router.post("/lessons/teachers/{teacher_id}/calendar/regenerate")
def regenerate_teacher_calendar(teacher_id: int, request: Request, user: dict = leader):
    with db.ward_connect(user["ward"]["id"]) as conn:
        t = _teacher_or_404(conn, teacher_id)
        les.new_teacher_token(conn, teacher_id)
    flash(request, "ok", f"New calendar link for {t['name']}. Send it to them; the old one stopped working.")
    return RedirectResponse(f"/lessons/teachers/{teacher_id}/calendar", status_code=303)


def _ward_or_none(ward_id: int) -> dict | None:
    # Look the ward up first: opening a ward database that doesn't exist would create it.
    with db.connect() as conn:
        return wards.get(conn, ward_id)


def _ics(body: bytes) -> Response:
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Cache-Control": "no-cache", "X-Robots-Tag": "noindex"})


@router.get("/feed/{ward_id}/lessons/{token}/{org}.ics")
def org_feed(ward_id: int, token: str, org: str):
    ward = _ward_or_none(ward_id)
    if ward is None or org not in les.ORGS:
        return Response(status_code=404)
    with db.ward_connect(ward_id) as conn:
        if not secrets.compare_digest(token, les.feed_token(conn)):
            return Response(status_code=404)
        return _ics(les.org_feed(conn, org, ward["name"]))


@router.get("/feed/{ward_id}/teacher/{token}.ics")
def teacher_feed(ward_id: int, token: str):
    ward = _ward_or_none(ward_id)
    if ward is None:
        return Response(status_code=404)
    with db.ward_connect(ward_id) as conn:
        t = les.teacher_by_token(conn, token)
        if t is None:
            return Response(status_code=404)
        return _ics(les.teacher_feed(conn, t, ward["name"]))


# --- Planning board -----------------------------------------------------------
# Registered last so /lessons/talks etc. match before /lessons/{plan}.

def _plan_or_404(plan: str) -> str:
    if plan not in les.PLAN_NAMES:
        raise HTTPException(status_code=404, detail="Not found")
    return plan


def _conference_or_404(conn, conference_id: int) -> dict:
    row = conn.execute("SELECT * FROM conferences WHERE id = ?", (conference_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Conference not found")
    return les.get_conference(conn, row["year"], row["month"])


@router.get("/lessons/{plan}", response_class=HTMLResponse)
def board(plan: str, request: Request, conf: str = "", user: dict = viewer):
    _plan_or_404(plan)
    year, month = les.parse_conf(conf)
    with db.ward_connect(user["ward"]["id"]) as conn:
        shared = les.is_shared(conn)
        if (plan == "ward") != shared:   # the ward switched between one plan and two
            return RedirectResponse(plan_url(les.plan_for(conn, "eq" if plan == "ward" else plan), year, month),
                                    status_code=303)
        record = les.get_conference(conn, year, month)
        if record is None:
            flash(request, "info", f"Load the {les.conference_name(year, month)} conference talks first."
                  if user["leads"] else f"The {les.conference_name(year, month)} talks aren’t loaded yet.")
            return RedirectResponse(talks_url(year, month), status_code=303)
        state = les.board_state(conn, plan, record) | {"readonly": not user["leads"]}
    return render(request, "lessons/board.html", user=user, state=state, plan=plan, shared=shared,
                  record=record, prev=les.prev_conference(year, month), next=les.next_conference(year, month),
                  current=les.conference_for(date.today()), org_names=les.ORGS)


# Before /api/lessons/{plan}/{conference_id}, which would otherwise match it.
@router.put("/api/lessons/days/{iso}")
async def api_day(iso: str, request: Request, user: dict = leader):
    body = await json_body(request)
    try:
        with db.ward_connect(user["ward"]["id"]) as conn:
            return les.set_day(conn, iso, str(body.get("status", "")), str(body.get("note", "")))
    except les.LessonError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.put("/api/lessons/{plan}/{conference_id}")
async def api_save(plan: str, conference_id: int, request: Request, user: dict = leader):
    _plan_or_404(plan)
    body = await json_body(request)
    try:
        with db.ward_connect(user["ward"]["id"]) as conn:
            conf = _conference_or_404(conn, conference_id)
            version = les.save_plan(conn, plan, conf, body.get("weeks"), int(body.get("version", -1)),
                                    user["username"])
    except les.VersionConflict:
        return JSONResponse({"error": "conflict"}, status_code=409)
    except (les.LessonError, TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"version": version}

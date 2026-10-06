import json
from datetime import date, timedelta
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import admin, auth, config, db, ministering, ministering_routes, wards
from .reports import birthdays, detect, directory
from .reports import ministering as ministering_report
from .web import BASE, absolute_url, flash, render


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.secret_key(),
    session_cookie="ward_portal",
    max_age=14 * 24 * 3600,
    same_site="lax",
    https_only=config.COOKIE_SECURE,
)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
app.include_router(admin.router)
app.include_router(ministering_routes.router)


@app.exception_handler(auth.LoginRequired)
def _login_redirect(request: Request, exc: auth.LoginRequired):
    return RedirectResponse("/login", status_code=303)


def _feed_url(request: Request, token: str) -> str:
    return absolute_url(request, f"/feed/{token}/birthdays.ics")


# --- Auth -------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if auth.current_user(request):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html")


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    user = auth.authenticate(username, password)
    if not user:
        return render(request, "login.html", error="Invalid username or password.", username=username)
    auth.log_in(request, user["id"])
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# --- Pages ------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: dict = Depends(auth.require_user)):
    with db.connect() as conn:
        users_count = conn.execute("SELECT COUNT(*) FROM users WHERE ward_id = ?",
                                   (user["ward"]["id"],)).fetchone()[0]
    with db.ward_connect(user["ward"]["id"]) as conn:
        soon = birthdays.upcoming(conn, days=7)
        bday_count = conn.execute("SELECT COUNT(*) FROM birthdays").fetchone()[0]
        flagged = conn.execute(
            "SELECT COUNT(*) FROM birthdays WHERE missing_since IS NOT NULL AND kept = 0").fetchone()[0]
        people = conn.execute("SELECT COUNT(*) FROM people WHERE active = 1").fetchone()[0]
        last_dir = conn.execute(
            "SELECT *, julianday('now') - julianday(uploaded_at) AS age FROM uploads "
            "WHERE report = 'directory' ORDER BY id DESC LIMIT 1").fetchone()
        orgs = [ministering.org_summary(conn, o) for o in ministering.ORGS] if user["leads"] else []

    moved_in = len(json.loads(last_dir["summary"]).get("roster", {}).get("moved_in", [])) if last_dir else 0
    todo = [{"text": f"Tell {b['name']} happy birthday!" + (f" They turn {b['turns']}." if b["turns"] else ""),
             "cta": "Birthdays", "href": "/birthdays"} for b in soon if b["days"] == 0]
    if flagged:
        todo.append({"text": f"{flagged} birthday{'s' if flagged > 1 else ''} may have moved out",
                     "cta": "Review", "href": "/birthdays#flagged"})
    for o in orgs:
        for l in o["proposed"]:
            todo.append({"text": f"Approve “{l['name']}” for {o['name']}", "cta": "Open",
                         "href": f"/ministering/layouts/{l['id']}"})
        for l in o["to_check"]:
            if l["check"]["state"] == "mismatch":
                n = l["check"]["differences"]
                todo.append({"text": f"“{l['name']}” doesn’t match LCR yet ({n} difference{'s' if n != 1 else ''})",
                             "cta": "Review", "href": f"/ministering/layouts/{l['id']}/changes"})
            else:
                todo.append({"text": f"Enter “{l['name']}” in LCR, then re-import the {o['name']} report "
                                     "to check it", "cta": "Check", "href": f"/ministering/layouts/{l['id']}/changes"})
        if o["current"] is None:
            todo.append({"text": f"Import the {o['name']} assignments from LCR", "cta": "Import",
                         "href": f"/ministering/{o['key']}#import"})
    if last_dir is None:
        todo.append({"text": "Upload the Member List from LCR", "cta": "Upload", "href": "/upload"})
    elif last_dir["age"] > 21:
        todo.append({"text": f"Member List is {int(last_dir['age'] // 7)} weeks old", "cta": "Upload",
                     "href": "/upload"})
    return render(request, "dashboard.html", user=user, soon=soon, bday_count=bday_count, orgs=orgs,
                  todo=todo, people=people, moved_in=moved_in, users_count=users_count)


@app.get("/birthdays", response_class=HTMLResponse)
def birthday_page(request: Request, month: str = "", user: dict = Depends(auth.require_user)):
    today = date.today()
    try:
        year, mon = (int(x) for x in month.split("-")) if month else (today.year, today.month)
        first = date(year, mon, 1)
    except ValueError:
        first = date(today.year, today.month, 1)
    prev = (first - timedelta(days=1)).replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    token = user["ward"]["feed_token"]
    with db.ward_connect(user["ward"]["id"]) as conn:
        weeks = birthdays.month_grid(conn, first.year, first.month)
        soon = birthdays.upcoming(conn, days=30)
        count = conn.execute("SELECT COUNT(*) FROM birthdays").fetchone()[0]
        flagged = [dict(r) | {"display": birthdays.display_name(r["name"])} for r in conn.execute(
            "SELECT id, name, missing_since FROM birthdays WHERE missing_since IS NOT NULL AND kept = 0 "
            "ORDER BY name COLLATE NOCASE")]
        last = conn.execute(
            "SELECT uploaded_at FROM uploads WHERE report IN ('birthdays', 'directory') ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return render(request, "birthdays.html", user=user, weeks=weeks, first=first, today=today,
                  prev=f"{prev:%Y-%m}", next=f"{nxt:%Y-%m}", soon=soon[:8], count=count,
                  in_month=sum(len(c["names"]) for w in weeks for c in w), flagged=flagged,
                  last=last["uploaded_at"] if last else None, feed_url=_feed_url(request, token))


@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request, user: dict = Depends(auth.require_user)):
    return render(request, "upload.html", user=user)


@app.post("/upload", response_class=HTMLResponse)
async def upload(request: Request, file: UploadFile, org: str = Form(""),
                 user: dict = Depends(auth.require_user)):
    """Accepts any supported LCR report and routes it by its title.

    Uploaded files are never written to disk; only the parsed rows are kept.
    """
    org = org if org in ministering.ORGS else ""  # set when uploading from an EQ/RS page
    back = f"/ministering/{org}" if org else "/upload"
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        flash(request, "error", "That file is too large.")
        return RedirectResponse(back, status_code=303)
    try:
        kind = detect(data)
        if kind == "ministering" and not user["leads"]:
            raise birthdays.ReportError("Only leaders can import ministering assignments.")
        parsed = {"directory": directory.parse_pdf, "birthdays": birthdays.parse_pdf,
                  "ministering": ministering_report.parse_pdf}[kind](data)
    except birthdays.ReportError as exc:
        flash(request, "error", str(exc))
        return RedirectResponse(back, status_code=303)

    if kind == "ministering":
        return _import_ministering(request, user, parsed, org or None)

    with db.ward_connect(user["ward"]["id"]) as conn:
        roster = None
        if kind == "directory":
            roster = ministering.sync_people(conn, parsed)
            bdays = ministering.directory_birthdays(parsed)
        else:
            bdays = parsed
        added, missing = birthdays.sync(conn, bdays)
        summary = {"total": len(bdays),
                   "added": [b.display_name for b in added],
                   "missing": [b.display_name for b in missing]}
        if roster:
            summary["roster"] = {k: [birthdays.display_name(n) for n in v] if isinstance(v, list) else v
                                 for k, v in roster.items()}
        conn.execute("INSERT INTO uploads (report, uploaded_by, summary) VALUES (?, ?, ?)",
                     (kind, user["username"], json.dumps(summary)))
    return render(request, "upload_result.html", user=user, summary=summary, kind=kind)


def _import_ministering(request: Request, user: dict, districts, hint: str | None) -> RedirectResponse:
    """The PDF doesn't say whether it's elders quorum or Relief Society, so tell from the ministers.

    `hint` is the page it was uploaded from; it only decides when the ministers don't.
    """
    with db.ward_connect(user["ward"]["id"]) as conn:
        if not conn.execute("SELECT 1 FROM people LIMIT 1").fetchone():
            flash(request, "error", "Upload the Member List (directory) first, so names can be matched.")
            return RedirectResponse(f"/ministering/{hint}" if hint else "/ministering", status_code=303)
        payload = ministering.match_names(conn, districts)
        org = ministering.infer_org(conn, payload) or hint
        payload["org"] = org
        if payload["unmatched"] or org is None:
            pending_id = ministering.save_pending(conn, user["username"], payload)
            return RedirectResponse(f"/ministering/import/{pending_id}", status_code=303)
        layout_id = ministering.finish_import(conn, payload, {}, user["username"], org)
        checked = ministering.check_approved(conn, org, layout_id)
        conn.execute("INSERT INTO uploads (report, uploaded_by, summary) VALUES ('ministering', ?, ?)",
                     (user["username"], json.dumps({"layout_id": layout_id, "org": org})))
    note = (f" (uploaded from the {ministering.ORGS[hint]} page, but the ministers are "
            f"{'brothers' if org == 'eq' else 'sisters'})") if hint and hint != org else ""
    flash(request, "ok", f"Imported the current {ministering.ORGS[org]} assignments{note}."
          + (" " + ministering.check_note(checked) if checked else ""))
    return RedirectResponse(ministering_routes.after_import(layout_id, checked), status_code=303)


@app.post("/birthdays/{birthday_id}/resolve")
def resolve_missing(birthday_id: int, request: Request, action: str = Form(...),
                    user: dict = Depends(auth.require_user)):
    if action not in ("remove", "keep"):
        return RedirectResponse("/", status_code=303)
    with db.ward_connect(user["ward"]["id"]) as conn:
        birthdays.resolve_missing(conn, birthday_id, action)
    return RedirectResponse("/birthdays#flagged", status_code=303)


@app.post("/birthdays/remove-missing")
def remove_all_missing(request: Request, user: dict = Depends(auth.require_user)):
    with db.ward_connect(user["ward"]["id"]) as conn:
        n = conn.execute("DELETE FROM birthdays WHERE missing_since IS NOT NULL AND kept = 0").rowcount
    flash(request, "ok", f"Removed {n} birthday(s) from the calendar.")
    return RedirectResponse("/birthdays", status_code=303)


@app.post("/feed/regenerate")
def regenerate_feed(request: Request, user: dict = Depends(auth.require_manager)):
    with db.connect() as conn:
        wards.new_feed_token(conn, user["ward"]["id"])
    flash(request, "ok", "New calendar link created. Everyone needs to subscribe again with the new link.")
    return RedirectResponse("/birthdays#subscribe", status_code=303)


# --- Public -----------------------------------------------------------------

@app.get("/feed/{token}/birthdays.ics")
def birthday_feed(token: str):
    with db.connect() as conn:
        ward = wards.by_feed_token(conn, token)
    if ward is None:
        return Response(status_code=404)
    with db.ward_connect(ward["id"]) as conn:
        rows = birthdays.load(conn)
    body = birthdays.build_ics(rows, f"{ward['name']} Birthdays")
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Cache-Control": "no-cache", "X-Robots-Tag": "noindex"})


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/robots.txt", response_class=Response)
def robots():
    return Response("User-agent: *\nDisallow: /\n", media_type="text/plain")

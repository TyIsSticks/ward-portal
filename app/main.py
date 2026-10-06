import hmac
import json
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import admin, auth, config, db
from .reports import birthdays
from .web import BASE, absolute_url, flash, render


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    with db.connect() as conn:
        if not db.get_setting(conn, "feed_token"):
            db.set_setting(conn, "feed_token", secrets.token_urlsafe(32))
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
        token = db.get_setting(conn, "feed_token")
        count = conn.execute("SELECT COUNT(*) FROM birthdays").fetchone()[0]
        last = conn.execute(
            "SELECT * FROM uploads WHERE report = 'birthdays' ORDER BY id DESC LIMIT 1").fetchone()
    return render(request, "dashboard.html", user=user, count=count,
                   last=dict(last) | {"summary": json.loads(last["summary"])} if last else None,
                   feed_url=_feed_url(request, token))


@app.get("/birthdays", response_class=HTMLResponse)
def birthday_list(request: Request, user: dict = Depends(auth.require_user)):
    with db.connect() as conn:
        rows = birthdays.load(conn)
    by_month: dict[int, list] = {}
    for b in rows:
        by_month.setdefault(b.month, []).append(b)
    return render(request, "birthdays.html", user=user, by_month=by_month, total=len(rows))


@app.post("/birthdays/upload", response_class=HTMLResponse)
async def birthday_upload(request: Request, file: UploadFile, user: dict = Depends(auth.require_user)):
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        flash(request, "error", "That file is too large.")
        return RedirectResponse("/", status_code=303)
    try:
        parsed = birthdays.parse_pdf(data)
    except birthdays.ReportError as exc:
        flash(request, "error", str(exc))
        return RedirectResponse("/", status_code=303)

    with db.connect() as conn:
        added, removed = birthdays.sync(conn, parsed)
        summary = {"total": len(parsed),
                   "added": [b.display_name for b in added],
                   "removed": [b.display_name for b in removed]}
        conn.execute("INSERT INTO uploads (report, uploaded_by, summary) VALUES (?, ?, ?)",
                     ("birthdays", user["username"], json.dumps(summary)))
    # The uploaded file is never written to disk; only parsed rows are kept.
    return render(request, "upload_result.html", user=user, summary=summary)


@app.post("/feed/regenerate")
def regenerate_feed(request: Request, user: dict = Depends(auth.require_admin)):
    with db.connect() as conn:
        db.set_setting(conn, "feed_token", secrets.token_urlsafe(32))
    flash(request, "ok", "New feed URL created. Re-subscribe in Google Calendar with the new link.")
    return RedirectResponse("/", status_code=303)


# --- Public -----------------------------------------------------------------

@app.get("/feed/{token}/birthdays.ics")
def birthday_feed(token: str):
    with db.connect() as conn:
        expected = db.get_setting(conn, "feed_token") or ""
        if not hmac.compare_digest(token, expected):
            return Response(status_code=404)
        rows = birthdays.load(conn)
    body = birthdays.build_ics(rows, f"{config.WARD_NAME} Birthdays")
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Cache-Control": "no-cache", "X-Robots-Tag": "noindex"})


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/robots.txt", response_class=Response)
def robots():
    return Response("User-agent: *\nDisallow: /\n", media_type="text/plain")

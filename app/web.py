"""Shared helpers for HTML routes."""
import os
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import auth, config, db, wards

BASE = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=BASE / "templates")
# Before sign-in there's no ward; pages then show the site name.
templates.env.globals["ward_name"] = config.SITE_NAME
templates.env.globals["ward_initials"] = wards.initials(config.SITE_NAME)
# Cache-buster for /static links, so browsers pick up new CSS/JS right after an update.
templates.env.globals["asset_v"] = str(int(max(f.stat().st_mtime for f in (BASE / "static").iterdir())))
templates.env.globals["month_names"] = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"]


def render(request: Request, name: str, status_code: int = 200, **ctx) -> HTMLResponse:
    user = ctx.setdefault("user", auth.current_user(request))
    if user:
        ctx.setdefault("ward_name", user["ward"]["name"])
        ctx.setdefault("ward_initials", wards.initials(user["ward"]["name"]))
        if user["is_admin"]:  # for the ward switcher
            with db.connect() as conn:
                ctx["all_wards"] = conn.execute("SELECT id, name FROM wards ORDER BY name COLLATE NOCASE").fetchall()
    ctx["flash"] = request.session.pop("flash", None)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def flash(request: Request, kind: str, message: str) -> None:
    request.session["flash"] = (kind, message)


def absolute_url(request: Request, path: str) -> str:
    base = os.environ.get("PUBLIC_URL") or str(request.base_url)
    return f"{base.rstrip('/')}{path}"

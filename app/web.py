"""Shared helpers for HTML routes."""
import os
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import auth, config

BASE = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=BASE / "templates")
templates.env.globals["ward_name"] = config.WARD_NAME
# Cache-buster for /static links, so browsers pick up new CSS/JS right after an update.
templates.env.globals["asset_v"] = str(int(max(f.stat().st_mtime for f in (BASE / "static").iterdir())))
# Logo mark: initials of the first two words ("Kays Creek YSA Ward" -> "KC").
templates.env.globals["ward_initials"] = "".join(w[0] for w in config.WARD_NAME.split()[:2]).upper() or "W"
templates.env.globals["month_names"] = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"]


def render(request: Request, name: str, status_code: int = 200, **ctx) -> HTMLResponse:
    ctx.setdefault("user", auth.current_user(request))
    ctx["flash"] = request.session.pop("flash", None)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def flash(request: Request, kind: str, message: str) -> None:
    request.session["flash"] = (kind, message)


def absolute_url(request: Request, path: str) -> str:
    base = os.environ.get("PUBLIC_URL") or str(request.base_url)
    return f"{base.rstrip('/')}{path}"

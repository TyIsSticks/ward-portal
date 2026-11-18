"""Status page for ward admins, with backups and the public-link test for admins."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from . import auth, backup, config, db, health
from .web import flash, render

router = APIRouter()


@router.get("/admin/status", response_class=HTMLResponse)
def status_page(request: Request, user: dict = Depends(auth.require_manager)):
    with db.ward_connect(user["ward"]["id"]) as conn:
        ward = health.ward_checks(conn)
    server = health.server_checks() if user["is_admin"] else []
    backups = backup.listing() if user["is_admin"] else []
    return render(request, "status.html", user=user, ward=ward, server=server, backups=backups,
                  size=health.size, public_url=config.PUBLIC_URL, keep=backup.KEEP)


@router.post("/admin/backups")
def make_backup(request: Request, user: dict = Depends(auth.require_admin)):
    try:
        path = backup.create("manual")
        flash(request, "ok", f"Backup made ({health.size(path.stat().st_size)}). Download it below.")
    except Exception as exc:   # disk full, unreadable file...: say so rather than a blank error page
        flash(request, "error", f"Couldn’t make a backup: {exc}")
    return RedirectResponse("/admin/status#backups", status_code=303)


@router.get("/admin/backups/{name}")
def download_backup(name: str, user: dict = Depends(auth.require_admin)):
    path = backup.path_of(name)
    if path is None:
        raise HTTPException(status_code=404, detail="Backup not found")
    return FileResponse(path, media_type="application/zip", filename=name,
                        headers={"Cache-Control": "no-store"})


@router.post("/admin/status/check-public")
def check_public(request: Request, user: dict = Depends(auth.require_admin)):
    if not config.PUBLIC_URL:
        flash(request, "error", "Set PUBLIC_URL in .env to test the public link.")
    else:
        ok, text = health.check_public_link(config.PUBLIC_URL)
        flash(request, "ok" if ok else "error", text)
    return RedirectResponse("/admin/status#public", status_code=303)

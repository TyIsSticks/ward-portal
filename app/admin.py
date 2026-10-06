"""User administration, invite/reset links, and self-service account settings."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import auth, db, users
from .web import absolute_url, flash, render

router = APIRouter()


def _back() -> RedirectResponse:
    return RedirectResponse("/admin/users", status_code=303)


def _role(role: str) -> str:
    return role if role in auth.ROLES else "member"


def _not_self(user: dict, user_id: int, request: Request) -> bool:
    if user["id"] == user_id:
        flash(request, "error", "You can't do that to your own account.")
        return False
    return True


# --- Admin: users & invites -------------------------------------------------

@router.get("/admin/users", response_class=HTMLResponse)
def users_page(request: Request, user: dict = Depends(auth.require_admin)):
    with db.connect() as conn:
        people = users.list_users(conn)
        invites = users.pending_invites(conn)
    new_link = request.session.pop("new_link", None)
    return render(request, "users.html", user=user, people=people, invites=invites,
                  new_link=new_link, ttl_days=users.LINK_TTL_DAYS, roles=auth.ROLES)


@router.post("/admin/invites")
def create_invite(request: Request, note: str = Form(""), role: str = Form("member"),
                  user: dict = Depends(auth.require_admin)):
    with db.connect() as conn:
        token = users.create_invite(conn, user["username"], role=_role(role), note=note)
    request.session["new_link"] = {
        "kind": "invite", "for": note.strip() or "a new user",
        "url": absolute_url(request, f"/invite/{token}")}
    return _back()


@router.post("/admin/invites/{invite_id}/revoke")
def revoke_invite(invite_id: int, request: Request, user: dict = Depends(auth.require_admin)):
    with db.connect() as conn:
        users.revoke(conn, invite_id)
    flash(request, "ok", "Invite revoked.")
    return _back()


@router.post("/admin/users/{user_id}/reset")
def reset_link(user_id: int, request: Request, user: dict = Depends(auth.require_admin)):
    with db.connect() as conn:
        target = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            return _back()
        token = users.create_reset(conn, user["username"], user_id)
    request.session["new_link"] = {
        "kind": "reset", "for": target["username"],
        "url": absolute_url(request, f"/invite/{token}")}
    return _back()


@router.post("/admin/users/{user_id}/role")
def set_role(user_id: int, request: Request, role: str = Form(...),
             user: dict = Depends(auth.require_admin)):
    if _not_self(user, user_id, request):
        with db.connect() as conn:
            users.set_role(conn, user_id, _role(role))
        flash(request, "ok", "Role updated.")
    return _back()


@router.post("/admin/users/{user_id}/delete")
def delete_user(user_id: int, request: Request, user: dict = Depends(auth.require_admin)):
    if _not_self(user, user_id, request):
        with db.connect() as conn:
            users.delete_user(conn, user_id)
        flash(request, "ok", "User removed.")
    return _back()


# --- Public: redeem an invite or reset link ---------------------------------

@router.get("/invite/{token}", response_class=HTMLResponse)
def invite_page(token: str, request: Request):
    with db.connect() as conn:
        link = users.lookup(conn, token)
    if link is None:
        return render(request, "invite.html", status_code=404, link=None)
    return render(request, "invite.html", link=link)


@router.post("/invite/{token}", response_class=HTMLResponse)
def redeem_invite(token: str, request: Request, password: str = Form(...),
                  confirm: str = Form(...), username: str = Form("")):
    try:
        auth.validate_password(password, confirm)
        with db.connect() as conn:
            user_id = users.redeem(conn, token, password, username=username)
    except ValueError as exc:  # includes LinkError; the transaction rolls back so the link stays usable
        with db.connect() as conn:
            link = users.lookup(conn, token)
        return render(request, "invite.html", status_code=400 if link else 404,
                      link=link, error=str(exc), username=username)
    auth.log_in(request, user_id)
    flash(request, "ok", "You're all set. Welcome!")
    return RedirectResponse("/", status_code=303)


# --- Self-service -----------------------------------------------------------

@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request, user: dict = Depends(auth.require_user)):
    return render(request, "account.html", user=user)


@router.post("/account/password", response_class=HTMLResponse)
def change_password(request: Request, current: str = Form(...), password: str = Form(...),
                    confirm: str = Form(...), user: dict = Depends(auth.require_user)):
    with db.connect() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
    if not auth.verify_password(current, row["password_hash"]):
        return render(request, "account.html", status_code=400, user=user,
                      error="Your current password was wrong.")
    try:
        auth.validate_password(password, confirm)
    except ValueError as exc:
        return render(request, "account.html", status_code=400, user=user, error=str(exc))
    with db.connect() as conn:
        auth.update_password(conn, user["id"], password)
    auth.log_in(request, user["id"])  # stay signed in here; other sessions are signed out
    flash(request, "ok", "Password changed. Any other devices have been signed out.")
    return RedirectResponse("/account", status_code=303)

"""Accounts and wards: user admin, invite/reset links, ward management, self-service settings.

Ward admins manage accounts in their own ward; admins manage every ward and create new ones.
"""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import auth, db, users, wards
from .web import absolute_url, flash, render

router = APIRouter()


def _back() -> RedirectResponse:
    return RedirectResponse("/admin/users", status_code=303)


def _assignable(user: dict) -> tuple[str, ...]:
    """Roles this user may hand out. Only admins can make other admins."""
    return auth.ROLES if user["is_admin"] else tuple(r for r in auth.ROLES if r != "admin")


def _role(user: dict, role: str) -> str:
    return role if role in _assignable(user) else "member"


def _target(conn, user: dict, user_id: int, request: Request) -> dict | None:
    """The account being changed, if it's in the current ward and this user may change it."""
    target = users.get_user(conn, user_id)
    if target is None or target["ward_id"] != user["ward"]["id"]:
        return None
    if target["id"] == user["id"]:
        flash(request, "error", "You can't do that to your own account.")
        return None
    if target["role"] == "admin" and not user["is_admin"]:
        flash(request, "error", "Only an admin can change an admin's account.")
        return None
    return target


# --- Users & invites (current ward) -----------------------------------------

@router.get("/admin/users", response_class=HTMLResponse)
def users_page(request: Request, user: dict = Depends(auth.require_manager)):
    ward_id = user["ward"]["id"]
    with db.connect() as conn:
        people = users.list_users(conn, ward_id)
        invites = users.pending_invites(conn, ward_id)
    new_link = request.session.pop("new_link", None)
    return render(request, "users.html", user=user, people=people, invites=invites, new_link=new_link,
                  ttl_days=users.LINK_TTL_DAYS, roles=_assignable(user), role_labels=auth.ROLE_LABELS)


@router.post("/admin/invites")
def create_invite(request: Request, note: str = Form(""), role: str = Form("member"),
                  user: dict = Depends(auth.require_manager)):
    with db.connect() as conn:
        token = users.create_invite(conn, user["username"], user["ward"]["id"], role=_role(user, role), note=note)
    request.session["new_link"] = {
        "kind": "invite", "for": note.strip() or "a new user",
        "url": absolute_url(request, f"/invite/{token}")}
    return _back()


@router.post("/admin/invites/{invite_id}/revoke")
def revoke_invite(invite_id: int, request: Request, user: dict = Depends(auth.require_manager)):
    with db.connect() as conn:
        users.revoke(conn, invite_id, user["ward"]["id"])
    flash(request, "ok", "Invite revoked.")
    return _back()


@router.post("/admin/users/{user_id}/reset")
def reset_link(user_id: int, request: Request, user: dict = Depends(auth.require_manager)):
    with db.connect() as conn:
        target = _target(conn, user, user_id, request)
        if not target:
            return _back()
        token = users.create_reset(conn, user["username"], user_id)
    request.session["new_link"] = {
        "kind": "reset", "for": target["username"],
        "url": absolute_url(request, f"/invite/{token}")}
    return _back()


@router.post("/admin/users/{user_id}/role")
def set_role(user_id: int, request: Request, role: str = Form(...),
             user: dict = Depends(auth.require_manager)):
    with db.connect() as conn:
        if _target(conn, user, user_id, request):
            users.set_role(conn, user_id, _role(user, role))
            flash(request, "ok", "Role updated.")
    return _back()


@router.post("/admin/users/{user_id}/delete")
def delete_user(user_id: int, request: Request, user: dict = Depends(auth.require_manager)):
    with db.connect() as conn:
        if _target(conn, user, user_id, request):
            users.delete_user(conn, user_id)
            flash(request, "ok", "User removed.")
    return _back()


# --- Wards (admins only) ----------------------------------------------------

@router.get("/admin/wards", response_class=HTMLResponse)
def wards_page(request: Request, user: dict = Depends(auth.require_admin)):
    with db.connect() as conn:
        rows = wards.list_wards(conn)
    for w in rows:
        w["people"] = wards.people_count(w["id"])
    new_link = request.session.pop("new_link", None)
    return render(request, "wards.html", user=user, wards=rows, new_link=new_link, ttl_days=users.LINK_TTL_DAYS)


@router.post("/admin/wards")
def create_ward(request: Request, name: str = Form(""), user: dict = Depends(auth.require_admin)):
    try:
        with db.connect() as conn:
            ward_id = wards.create(conn, name)
    except ValueError as exc:
        flash(request, "error", str(exc))
        return RedirectResponse("/admin/wards", status_code=303)
    flash(request, "ok", "Ward created. Invite its ward admin below.")
    return RedirectResponse(f"/admin/wards#ward-{ward_id}", status_code=303)


@router.post("/admin/wards/{ward_id}/rename")
def rename_ward(ward_id: int, request: Request, name: str = Form(""), user: dict = Depends(auth.require_admin)):
    try:
        with db.connect() as conn:
            wards.rename(conn, ward_id, name)
        flash(request, "ok", "Ward renamed.")
    except ValueError as exc:
        flash(request, "error", str(exc))
    return RedirectResponse("/admin/wards", status_code=303)


@router.post("/admin/wards/{ward_id}/switch")
def switch_ward(ward_id: int, request: Request, user: dict = Depends(auth.require_admin)):
    with db.connect() as conn:
        ward = wards.get(conn, ward_id)
    if ward:
        request.session["ward_id"] = ward_id
        flash(request, "ok", f"Now working in {ward['name']}.")
    return RedirectResponse("/", status_code=303)


@router.post("/admin/wards/{ward_id}/invite")
def invite_ward_admin(ward_id: int, request: Request, note: str = Form(""), user: dict = Depends(auth.require_admin)):
    with db.connect() as conn:
        ward = wards.get(conn, ward_id)
        if not ward:
            return RedirectResponse("/admin/wards", status_code=303)
        token = users.create_invite(conn, user["username"], ward_id, role="ward_admin", note=note)
    request.session["new_link"] = {
        "kind": "invite", "for": f"{note.strip() or 'a ward admin'} ({ward['name']})",
        "url": absolute_url(request, f"/invite/{token}")}
    return RedirectResponse("/admin/wards", status_code=303)


# --- Public: redeem an invite or reset link ---------------------------------

def _ward_ctx(link: dict | None) -> dict:
    """Show the invited person the ward they're joining (they aren't signed in yet)."""
    if link and link.get("ward_name"):
        return {"ward_name": link["ward_name"], "ward_initials": wards.initials(link["ward_name"])}
    return {}


@router.get("/invite/{token}", response_class=HTMLResponse)
def invite_page(token: str, request: Request):
    with db.connect() as conn:
        link = users.lookup(conn, token)
    if link is None:
        return render(request, "invite.html", status_code=404, link=None)
    return render(request, "invite.html", link=link, **_ward_ctx(link))


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
                      link=link, error=str(exc), username=username, **_ward_ctx(link))
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

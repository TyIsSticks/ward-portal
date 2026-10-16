"""Setting up the GroupMe bots: the birthday bot (ward admins) and lesson bots (leaders)."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import auth, db, groupme as gm
from .web import flash

router = APIRouter()

BACK = {"birthdays": "/birthdays#groupme", "lessons-eq": "/lessons/calendar#groupme-eq",
        "lessons-rs": "/lessons/calendar#groupme-rs"}


@router.post("/groupme/{key}")
def save(key: str, request: Request, action: str = Form("save"), bot_id: str = Form(""), style: str = Form(""),
         user: dict = Depends(auth.require_user)):
    if key not in gm.BOTS:
        raise HTTPException(status_code=404, detail="Not found")
    if not (user["manages"] if key == "birthdays" else user["leads"]):
        raise HTTPException(status_code=403, detail="Not allowed")
    name = gm.BOTS[key]
    try:
        with db.ward_connect(user["ward"]["id"]) as conn:
            if action == "remove":
                gm.remove(conn, key)
                flash(request, "ok", f"Turned off {name.lower()}. You can delete the bot in GroupMe too.")
            elif action == "test":
                gm.send_test(conn, key, gm.now().date())
                flash(request, "ok", "Sent a test message. Check the group chat.")
            else:
                if bot_id.strip():
                    gm.set_bot_id(conn, key, bot_id)
                elif not gm.bot_id(conn, key):
                    raise gm.GroupMeError("Paste the bot ID first.")
                if key == "birthdays" and style:
                    gm.set_style(conn, style)
                when = (f"at {gm.BIRTHDAY_HOUR} am on each birthday" if key == "birthdays"
                        else f"every Monday at {gm.LESSON_HOUR} am")
                flash(request, "ok", f"Saved. {name} go out {when}.")
    except gm.GroupMeError as exc:
        flash(request, "error", str(exc))
    return RedirectResponse(BACK[key], status_code=303)

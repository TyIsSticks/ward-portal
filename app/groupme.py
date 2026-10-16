"""GroupMe bots: birthday shout-outs and Monday lesson reminders, posted to a ward's group chats.

A GroupMe bot is made at dev.groupme.com/bots and posts into one group; all the portal needs is
its bot ID. Each ward can have three: one for birthdays and one per organization for lessons.
A background loop (started with the app) checks every few minutes and posts at most once a day
per bot. Messages only use first and last names, the same things the birthday calendar shows.
"""
import asyncio
import json
import logging
import random
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import config, db, lessons as les
from .reports import birthdays

log = logging.getLogger(__name__)

API = "https://api.groupme.com/v3/bots/post"
MAX_LEN = 1000                 # GroupMe's limit for a bot message

BOTS = {"birthdays": "Birthday shout-outs",
        "lessons-eq": "Elders Quorum lesson reminders",
        "lessons-rs": "Relief Society lesson reminders"}
BIRTHDAY_HOUR = 8              # birthday shout-outs go out from 8 am on the day
LESSON_HOUR = 9                # lesson reminders go out from 9 am on Monday
CHECK_EVERY = 5 * 60           # seconds between checks
START_DELAY = 30               # let the app finish starting before the first check
MAX_TRIES = 3                  # failed posts retried per day, so an outage doesn't mean silence


class GroupMeError(Exception):
    pass


def now() -> datetime:
    try:
        return datetime.now(ZoneInfo(config.TIMEZONE))
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now()


# --- Settings ---------------------------------------------------------------
# Stored in the ward's own ward_settings: groupme:<key>:bot_id, :style, :sent (date), :last (JSON).

def bot_id(conn: sqlite3.Connection, key: str) -> str | None:
    return db.get_ward_setting(conn, f"groupme:{key}:bot_id") or None


def set_bot_id(conn: sqlite3.Connection, key: str, value: str) -> None:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9]{10,64}", value):
        raise GroupMeError("That doesn’t look like a GroupMe bot ID. Copy the Bot ID from dev.groupme.com/bots.")
    db.set_ward_setting(conn, f"groupme:{key}:bot_id", value)


def remove(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM ward_settings WHERE key LIKE ?", (f"groupme:{key}:%",))


def style(conn: sqlite3.Connection) -> str:
    s = db.get_ward_setting(conn, "groupme:birthdays:style")
    return s if s in STYLES or s == "mix" else "mix"


def set_style(conn: sqlite3.Connection, value: str) -> None:
    if value not in STYLES and value != "mix":
        raise GroupMeError("Unknown style.")
    db.set_ward_setting(conn, "groupme:birthdays:style", value)


def status(conn: sqlite3.Connection, key: str) -> dict:
    """What the settings card shows: whether it's set up, and how the last post went."""
    bid = bot_id(conn, key)
    last = json.loads(db.get_ward_setting(conn, f"groupme:{key}:last") or "null")
    return {"key": key, "name": BOTS[key], "configured": bool(bid),
            "masked": f"••••{bid[-4:]}" if bid else "", "last": last}


def _record(conn: sqlite3.Connection, key: str, today: date, error: str | None) -> None:
    prev = json.loads(db.get_ward_setting(conn, f"groupme:{key}:last") or "null") or {}
    fails = prev.get("fails", 0) + 1 if error and prev.get("date") == today.isoformat() else int(bool(error))
    t = now()
    db.set_ward_setting(conn, f"groupme:{key}:last", json.dumps(
        {"date": today.isoformat(), "at": f"{t:%a, %b} {t.day} at {t:%I:%M %p}".replace(" 0", " "), "ok": not error,
         "error": error or "", "fails": fails}))
    if not error:
        db.set_ward_setting(conn, f"groupme:{key}:sent", today.isoformat())


def _done_today(conn: sqlite3.Connection, key: str, today: date) -> bool:
    if db.get_ward_setting(conn, f"groupme:{key}:sent") == today.isoformat():
        return True
    last = json.loads(db.get_ward_setting(conn, f"groupme:{key}:last") or "null") or {}
    return last.get("date") == today.isoformat() and last.get("fails", 0) >= MAX_TRIES


# --- Posting ----------------------------------------------------------------

def post(bot: str, text: str) -> None:
    body = json.dumps({"bot_id": bot, "text": text[:MAX_LEN]}).encode()
    req = urllib.request.Request(API, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "ward-portal"})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            if res.status >= 300:
                raise GroupMeError(f"GroupMe answered {res.status}.")
    except urllib.error.HTTPError as exc:
        hint = " Check the bot ID." if exc.code in (400, 404) else ""
        raise GroupMeError(f"GroupMe said no ({exc.code}).{hint}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GroupMeError(f"Couldn’t reach GroupMe ({exc}).") from None


# --- Birthday messages --------------------------------------------------------
# {names} "Ann Apple and Bo Birch", {NAMES} shouted, {s} verb ending, {is}/{has}/{was}, {legend}(s).

STYLES = {
    "trailer": ("Movie trailer", [
        "🎬 IN A WORLD… where the cake is plentiful and the candles are many…\n\n"
        "ONE WARD dared to celebrate.\n\n{NAMES}.\n\nTHE BIRTHDAY.\n\nNow showing everywhere. 🍿🎂",
        "🎥 From the ward that brought you funeral potatoes and the 20-minute closing hymn…\n"
        "comes a story of courage. Of frosting. Of one more lap around the sun.\n\n"
        "Starring {names}.\n\nHappy birthday! Rated E for Everyone Eats Cake. 🎂",
        "🎞️ They said it couldn’t be done. They said another year was impossible.\n\n"
        "But {names} just did it AGAIN.\n\nComing soon… no wait, it’s TODAY. The {legend} continue{s}. "
        "Happy birthday! 🎂🔥",
        "🎬 *epic music swells*\n\nOne ward. One calendar. One very special day.\n\n"
        "{names}… this is YOUR day. Happy birthday! 🎂✨",
    ]),
    "saga": ("Epic saga", [
        "📜 Hear ye, hear ye! Let the trumpets sound from every steeple: on this day {names} {has} "
        "completed another glorious lap around the sun! May the cake be vast, the candles many, and the "
        "singing only slightly off-key. 🎂⚔️",
        "🐉 Sing, O bards, of {names}, who braved another year of potlucks, ministering visits and "
        "chairs to stack, and emerged VICTORIOUS! Raise your goblets of Martinelli’s! 🥂🎂",
        "🏰 Long ago, in a ward not so far away, {names} {was} born. The prophecy foretold that this "
        "day would return every year. TODAY IS THAT DAY. Happy birthday! 👑🎂",
    ]),
    "hype": ("Stadium announcer", [
        "🏟️ LAAAADIES AND GENTLEMEN! Put your hands together for today’s birthday {legend}… "
        "{NAMES}!!! 🎉🎂🔥",
        "📣 AND THE CROWD GOES WIIIILD! {names} just leveled up another year and {is} looking "
        "UNSTOPPABLE! Happy birthday!! 🏆🎂",
        "🎤 It’s game day, folks! {names} {is} officially one year more legendary. Everybody in the "
        "chat, let’s hear it: HAPPY BIRTHDAY! 🙌🎂",
    ]),
    "droid": ("Battle droid", [
        "🤖 Roger roger. Scanning ward records… birthday detected: {names}. Deploying cake. 🎂",
        "🤖 Attention all units: {names} {is} now one year older. Orders are to celebrate. Roger roger. 🎉",
        "🤖 Roger roger. Happy birthday, {names}. This message will not self-destruct. 🎂",
    ]),
}


def short_name(display: str) -> str:
    """'Ann Marie Apple' -> 'Ann Apple'."""
    parts = display.split()
    return f"{parts[0]} {parts[-1]}" if len(parts) > 2 else display


def join_names(names: list[str]) -> str:
    if len(names) <= 2:
        return " and ".join(names)
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def birthday_message(names: list[str], which: str = "mix", rng: random.Random | None = None) -> str | None:
    if not names:
        return None
    rng = rng or random.Random()
    which = which if which in STYLES else rng.choice(list(STYLES))
    one = len(names) == 1
    joined = join_names(names)
    return rng.choice(STYLES[which][1]).format(
        names=joined, NAMES=joined.upper(), s="s" if one else "", legend="legend" if one else "legends",
        **{"is": "is" if one else "are", "has": "has" if one else "have", "was": "was" if one else "were"})


def todays_birthdays(conn: sqlite3.Connection, today: date) -> list[str]:
    """Short names of everyone whose birthday is today, leaving out people flagged as moved out."""
    gone = {b.display_name for b in birthdays.missing(conn)}
    return [short_name(b["name"]) for b in birthdays.upcoming(conn, days=0, today=today) if b["name"] not in gone]


# --- Lesson reminders (battle droid) -----------------------------------------
# Every message opens with a "roger roger"; the rest is shuffled so Mondays don't get stale.

OPENERS = [
    "🤖 Roger roger.",
    "🤖 Roger roger. Incoming transmission from command.",
    "🤖 Roger roger. Scanning the lesson schedule…",
    "🤖 Attention, {org} unit. Roger roger.",
    "🤖 Uh… roger roger? Yes. Roger roger.",
    "🤖 Roger roger. Weekly briefing follows.",
    "🤖 Roger roger. Clankers, assemble.",
]
SIGNOFFS = [
    "Read the talk before Sunday. That is an order.",
    "Report for duty in the second hour.",
    "End transmission.",
    "Move along. Move along. 👋",
    "Bring your scriptures. Bring a friend. Do not bring a lightsaber.",
    "Roger roger. 👍",
    "This briefing will now self-destruct. Just kidding. Roger roger.",
]
HEADERS = ["📡 Briefing for Sunday, {when}:", "🗓️ Mission for Sunday, {when}:",
           "🎯 Target acquired: Sunday, {when}.", "📋 Orders for Sunday, {when}:"]
TEACHER = ["🎓 Instructor: {who}", "🎓 Commanding officer: {who}", "🎓 Unit leader: {who}"]
NO_TEACHER = ["🎓 Instructor: unassigned. Volunteers will be… volunteered.",
              "🎓 Instructor: [CLASSIFIED] (not assigned yet)."]
NO_TALK = ["📖 Talk: not picked yet. Awaiting orders from the presidency.",
           "📖 Talk: [DATA MISSING]. Stand by for orders."]
NOTHING = ["Orders for Sunday, {when} have not been received yet. Stand by.",
           "No lesson on the books for Sunday, {when}. Command is… working on it.",
           "Scanning Sunday, {when}… no lesson found. Uh oh."]
NO_LESSON = {
    "fifth_sunday": ["Fifth Sunday detected ({when}). No regular {org} lesson. Enjoy your shore leave.",
                     "Sunday, {when} is a fifth Sunday. Standard lesson protocol suspended."],
    "stake_conference": ["Stake conference on Sunday, {when}. All units report to the stake center. "
                         "No {org} lesson.",
                         "Sunday, {when}: stake conference. No {org} lesson. Repeat: NO lesson."],
    "no_second_hour": ["No second hour on Sunday, {when}. Stand down after sacrament meeting.",
                       "Sunday, {when}: no second hour. No {org} lesson. Fall back after sacrament meeting."],
    "conference": ["General conference this weekend! All units report to the couch. 🛋️ No {org} lesson.",
                   "Sunday, {when} is general conference. Deploy snacks. Listen to the prophet. "
                   "No {org} lesson."],
}


def coming_sunday(today: date) -> date:
    """The next Sunday after today (a week out if today is Sunday)."""
    return today + timedelta(days=6 - today.weekday() or 7)


def lesson_message(conn: sqlite3.Connection, org: str, sunday: date, rng: random.Random | None = None) -> str:
    rng = rng or random.Random()
    fmt = {"org": les.ORGS[org], "when": f"{sunday:%b} {sunday.day}"}
    w = les.sunday(conn, org, sunday)
    lines = [rng.choice(OPENERS).format(**fmt), ""]
    if sunday.month in (4, 10) and sunday == les.conference_sunday(sunday.year, sunday.month):
        lines.append(rng.choice(NO_LESSON["conference"]).format(**fmt))
    elif w and w["status"] in NO_LESSON:
        lines.append(rng.choice(NO_LESSON[w["status"]]).format(**fmt))
        if w["day_note"]:
            lines.append(f"📝 {w['day_note']}")
    elif not w or not (w["talks"] or w["teacher_names"] or w["note"]):
        lines.append(rng.choice(NOTHING).format(**fmt))
    else:
        lines.append(rng.choice(HEADERS).format(**fmt))
        if len(w["talks"]) > 1:
            lines.append("📖 Combined lesson:")
        for t in w["talks"]:
            lines += [f"📖 “{t['title']}” by {t['speaker']}", f"🔗 {t['url']}"]
        if not w["talks"]:
            lines.append(rng.choice(NO_TALK))
        who = join_names(w["teacher_names"])
        lines.append(rng.choice(TEACHER).format(who=who) if who else rng.choice(NO_TEACHER))
        if w["note"]:
            lines.append(f"📝 {w['note']}")
    lines += ["", rng.choice(SIGNOFFS)]
    return "\n".join(lines)


# --- What to send ---------------------------------------------------------------

def preview(conn: sqlite3.Connection, key: str, today: date) -> str | None:
    """The message this bot would send for `today` (lessons: for the coming Sunday)."""
    if key == "birthdays":
        return birthday_message(todays_birthdays(conn, today), style(conn))
    return lesson_message(conn, key.removeprefix("lessons-"), coming_sunday(today))


def due(conn: sqlite3.Connection, moment: datetime) -> list[tuple[str, str, str]]:
    """(key, bot id, text) for every bot of this ward that should post now."""
    today, out = moment.date(), []
    for key in BOTS:
        bid = bot_id(conn, key)
        if not bid or _done_today(conn, key, today):
            continue
        if key == "birthdays":
            if moment.hour < BIRTHDAY_HOUR:
                continue
        elif moment.weekday() != 0 or moment.hour < LESSON_HOUR:
            continue
        text = preview(conn, key, today)
        if text:
            out.append((key, bid, text))
    return out


def run_due(moment: datetime | None = None) -> int:
    """Post everything that's due in every ward. Returns how many messages went out."""
    moment = moment or now()
    with db.connect() as conn:
        ward_ids = [r[0] for r in conn.execute("SELECT id FROM wards")]
    sent = 0
    for wid in ward_ids:
        with db.ward_connect(wid) as conn:
            jobs = due(conn, moment)
        for key, bid, text in jobs:   # posted outside the transaction: the network can be slow
            try:
                post(bid, text)
                error = None
                sent += 1
            except GroupMeError as exc:
                error = str(exc)
                log.warning("GroupMe %s for ward %s: %s", key, wid, error)
            with db.ward_connect(wid) as conn:
                _record(conn, key, moment.date(), error)
    return sent


def send_test(conn: sqlite3.Connection, key: str, today: date) -> None:
    """Post what the bot would send now, marked as a test (birthdays use a sample name if nobody has one today)."""
    bid = bot_id(conn, key)
    if not bid:
        raise GroupMeError("Add the bot ID first.")
    text = preview(conn, key, today) or birthday_message(["Sam Sample"], style(conn))
    post(bid, f"🧪 Test from the ward portal:\n\n{text}")


async def run_forever() -> None:
    await asyncio.sleep(START_DELAY)
    while True:
        try:
            await asyncio.to_thread(run_due)
        except Exception:   # never let one bad check stop the loop
            log.exception("GroupMe check failed")
        await asyncio.sleep(CHECK_EVERY)

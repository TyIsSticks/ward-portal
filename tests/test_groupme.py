"""GroupMe bots: messages, when they go out, and the settings forms. Made-up names only; nothing is posted."""
import random
from datetime import date, datetime

import pytest

from app import auth, db, groupme as gm, lessons as les
from tests.conftest import login
from tests.test_lessons import add_people, conf  # noqa: F401  (fixture)

BOT = "abcdef0123456789abcdef0123"


@pytest.fixture
def posted(monkeypatch):
    sent = []
    monkeypatch.setattr(gm, "post", lambda bot, text: sent.append((bot, text)))
    return sent


def test_birthday_messages_every_style():
    for key, (_, templates) in gm.STYLES.items():
        for i in range(len(templates) * 4):
            one = gm.birthday_message(["Ann Apple"], key, random.Random(i))
            many = gm.birthday_message(["Ann Apple", "Bo Birch", "Cy Cedar"], key, random.Random(i))
            assert ("Ann Apple" in one or "ANN APPLE" in one) and "{" not in one
            assert "Bo Birch, and Cy Cedar" in many or "BO BIRCH, AND CY CEDAR" in many
    assert gm.birthday_message([], "hype") is None
    assert gm.short_name("Ann Marie Apple") == "Ann Apple"


def test_coming_sunday():
    assert gm.coming_sunday(date(2030, 10, 14)) == date(2030, 10, 20)   # Monday
    assert gm.coming_sunday(date(2030, 10, 20)) == date(2030, 10, 27)   # Sunday: the next one


def test_lesson_messages_always_say_roger_roger(conf):
    with db.ward_connect(1) as conn:
        ids = add_people(conn)
        al = les.add_teacher(conn, "eq", person_id=ids["Able, Al"])
        les.save_plan(conn, "eq", conf, [{"date": "2030-10-20", "talks": conf["talk_ids"][1:2],
                                          "teachers": {"eq": [al["id"]]}}], 0, "admin")
        les.set_day(conn, "2030-11-10", "stake_conference")
        cases = {date(2030, 10, 20): "Al Able", date(2030, 10, 27): "Sunday, Oct 27",
                 date(2030, 11, 10): "stake", date(2030, 12, 29): "ifth Sunday", date(2031, 4, 6): "eneral conference"}
        for day, expect in cases.items():
            for seed in range(30):
                text = gm.lesson_message(conn, "eq", day, random.Random(seed))
                assert "roger roger" in text.lower() and expect in text and "{" not in text
        planned = gm.lesson_message(conn, "eq", date(2030, 10, 20))
        assert "https://" in planned and "“" in planned


def test_birthday_bot_posts_once_after_8am(client, posted):
    with db.ward_connect(1) as conn:
        conn.execute("INSERT INTO birthdays (name, month, day) VALUES ('Apple, Ann Marie', 3, 14)")
        conn.execute("INSERT INTO birthdays (name, month, day, missing_since) VALUES ('Gone, Gus', 3, 14, 'x')")
        gm.set_bot_id(conn, "birthdays", BOT)
    assert gm.run_due(datetime(2031, 3, 14, 7, 59)) == 0
    assert gm.run_due(datetime(2031, 3, 14, 8, 5)) == 1
    assert gm.run_due(datetime(2031, 3, 14, 12, 0)) == 0          # once a day
    assert gm.run_due(datetime(2031, 3, 15, 9, 0)) == 0           # nobody's birthday
    assert posted[0][0] == BOT and "Ann" in posted[0][1].title() and "Gus" not in posted[0][1]


def test_lesson_bot_posts_on_monday_and_retries_failures(client, conf, monkeypatch):
    with db.ward_connect(1) as conn:
        gm.set_bot_id(conn, "lessons-eq", BOT)
    calls = []

    def flaky(bot, text):
        calls.append(text)
        if len(calls) == 1:
            raise gm.GroupMeError("Couldn’t reach GroupMe.")
    monkeypatch.setattr(gm, "post", flaky)
    assert gm.run_due(datetime(2030, 10, 15, 10, 0)) == 0          # Tuesday
    assert gm.run_due(datetime(2030, 10, 14, 8, 0)) == 0           # Monday, too early
    assert gm.run_due(datetime(2030, 10, 14, 9, 0)) == 0           # fails, recorded
    with db.ward_connect(1) as conn:
        assert gm.status(conn, "lessons-eq")["last"]["ok"] is False
    assert gm.run_due(datetime(2030, 10, 14, 9, 5)) == 1           # retried
    assert gm.run_due(datetime(2030, 10, 14, 9, 10)) == 0
    assert "Oct 20" in calls[-1] and len(calls) == 2


def test_settings_forms(client, posted):
    login(client)
    assert "doesn’t look like" in client.post("/groupme/birthdays", data={"bot_id": "nope"}).text
    client.post("/groupme/birthdays", data={"bot_id": BOT, "style": "saga"})
    page = client.get("/birthdays").text
    assert "••••" + BOT[-4:] in page and BOT not in page and "Epic saga" in page
    client.post("/groupme/birthdays", data={"action": "test"})
    assert posted and posted[0][1].startswith("🧪 Test")
    client.post("/groupme/lessons-eq", data={"bot_id": BOT})
    assert "roger roger" in client.get("/lessons/calendar").text.lower()
    client.post("/groupme/birthdays", data={"action": "remove"})
    with db.ward_connect(1) as conn:
        assert gm.bot_id(conn, "birthdays") is None and gm.bot_id(conn, "lessons-eq") == BOT
    assert client.post("/groupme/nope", data={}).status_code == 404


def test_who_can_set_up_bots(client):
    auth.create_user("lead", "a long enough password", role="leader")
    login(client, "lead", "a long enough password")
    assert client.post("/groupme/birthdays", data={"bot_id": BOT}).status_code == 403   # ward admins only
    assert client.post("/groupme/lessons-rs", data={"bot_id": BOT}, follow_redirects=False).status_code == 303
    assert "Birthday shout-outs" not in client.get("/birthdays").text

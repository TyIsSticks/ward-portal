"""Move-in welcome checklist and ministering interviews. Made-up names only."""
from datetime import date

import pytest

from app import auth, db, interviews as iv, ministering as m, welcome
from tests.conftest import login
from tests.test_ministering import ROSTER, PW, ids, member, report_of

# A date well inside a quarter, so "this quarter" doesn't depend on when the tests run.
Q4 = date(2026, 11, 18)
TODAY = date.today().isoformat()


@pytest.fixture
def conn(client):
    with db.ward_connect(1) as c:
        m.sync_people(c, ROSTER)
        c.commit()
        yield c


def import_eq(conn, *groups):
    payload = m.match_names(conn, report_of(*groups))
    return m.finish_import(conn, payload, {}, "admin", "eq")


# --- welcome checklist ------------------------------------------------------

def test_move_ins_land_on_the_list_and_ministers_tick_themselves(conn):
    m.sync_people(conn, ROSTER + [member("New, Nia", "F", 5, 5, 1990), member("Kid, Ken", "M", 5, 5, date.today().year - 5)])
    rows = welcome.checklist(conn)
    assert {r["person"]["name"] for r in rows["open"]} == {"New, Nia", "Kid, Ken"}
    by = {r["person"]["name"]: r for r in rows["open"]}
    assert by["New, Nia"]["ministers"] == "unknown"      # nothing imported from LCR yet
    assert by["Kid, Ken"]["ministers"] == "na"           # under 18

    # An RS import that puts Nia in a group gives her ministers.
    payload = m.match_names(conn, report_of((["Dunn, Di", "Ford, Fay"], ["New, Nia"])))
    m.finish_import(conn, payload, {}, "admin", "rs")
    by = {r["person"]["name"]: r for r in welcome.checklist(conn)["open"]}
    assert by["New, Nia"]["ministers"] == "yes"


def test_checking_everything_completes_a_move_in(conn):
    m.sync_people(conn, ROSTER + [member("New, Nia", "F", 5, 5, 1990)])
    nia = ids(conn)["New, Nia"]
    m.finish_import(conn, m.match_names(conn, report_of((["Dunn, Di", "Ford, Fay"], ["New, Nia"]))), {}, "a", "rs")
    welcome.set_step(conn, nia, "contacted", True)
    assert welcome.summary(conn) == {"open": 1, "no_ministers": 0, "to_welcome": 1}
    welcome.set_step(conn, nia, "calling", True)
    rows = welcome.checklist(conn)
    assert not rows["open"] and [r["person"]["name"] for r in rows["done"]] == ["New, Nia"]
    welcome.set_step(conn, nia, "calling", False)   # undo puts them back
    assert len(welcome.checklist(conn)["open"]) == 1
    with pytest.raises(welcome.WelcomeError):
        welcome.set_step(conn, nia, "nonsense", True)
    with pytest.raises(welcome.WelcomeError):
        welcome.set_step(conn, 9999, "calling", True)


def test_people_tagged_before_the_list_existed_are_picked_up_and_dismissal_sticks(conn):
    able = ids(conn)["Able, Al"]
    m.set_tags(conn, able, ["New move-in"])
    assert [r["person"]["name"] for r in welcome.checklist(conn)["open"]] == ["Able, Al"]
    welcome.dismiss(conn, able)
    assert welcome.checklist(conn) == {"open": [], "done": []}
    m.sync_people(conn, ROSTER[1:])                      # moving out removes them from the list too
    assert welcome.checklist(conn)["open"] == []


def test_welcome_page_and_to_do(client, conn):
    m.sync_people(conn, ROSTER + [member("New, Nia", "F", 5, 5, 1990)])
    conn.commit()
    m.finish_import(conn, m.match_names(conn, report_of((["Dunn, Di", "Ford, Fay"], ["Eads, Em"]))), {}, "a", "rs")
    conn.commit()
    nia = ids(conn)["New, Nia"]
    login(client)
    page = client.get("/")
    assert "1 move-in has no ministers yet" in page.text and "1 move-in still to welcome" in page.text
    r = client.get("/welcome")
    assert r.status_code == 200 and "New, Nia".replace("New, Nia", "Nia New") in r.text and "No ministers yet" in r.text
    client.post(f"/welcome/{nia}/step", data={"step": "contacted", "done": "1"})
    assert "Contacted" in client.get("/welcome").text
    assert client.post(f"/welcome/{nia}/step", data={"step": "bogus"}).status_code == 200   # flashes, doesn't crash
    client.post(f"/welcome/{nia}/dismiss")
    assert "Nobody to welcome" in client.get("/welcome").text


def test_only_leaders_see_the_welcome_list(client):
    auth.create_user("member", PW)
    login(client, "member", PW)
    assert client.get("/welcome").status_code == 403
    assert client.post("/welcome/1/step", data={"step": "calling"}).status_code == 403


# --- interviews: quarters and who's due -------------------------------------

def test_quarters():
    assert iv.quarter(date(2026, 11, 18)) == {"start": date(2026, 10, 1), "end": date(2026, 12, 31), "label": "Oct–Dec 2026"}
    assert iv.quarter(date(2027, 1, 1))["start"] == date(2027, 1, 1)
    q = iv.quarter(date(2026, 6, 30))
    assert (q["start"], q["end"]) == (date(2026, 4, 1), date(2026, 6, 30))


def test_who_is_due_this_quarter(conn):
    import_eq(conn, (["Able, Al", "Baker, Bo"], ["Dunn, Di"]), (["Cole, Cy"], []))
    p = ids(conn)
    g = iv.groups(conn, "eq", Q4)
    assert (g["total"], g["done"]) == (2, 0)
    assert iv.groups(conn, "rs", Q4)["current"] is None

    last_quarter = date(2026, 9, 20).isoformat()
    iv.create(conn, "eq", last_quarter, [p["Able, Al"], p["Baker, Bo"]], "Old", "", "ty")
    assert iv.groups(conn, "eq", Q4)["done"] == 0        # an interview from last quarter doesn't count

    iv.create(conn, "eq", "2026-11-02", [p["Able, Al"]], "Just Al", "", "ty")
    assert iv.groups(conn, "eq", Q4)["done"] == 0        # Bo still hasn't been interviewed
    iv.create(conn, "eq", "2026-11-09", [p["Baker, Bo"]], "Just Bo", "", "ty")
    g = iv.groups(conn, "eq", Q4)
    assert g["done"] == 1 and g["districts"][0]["groups"][0]["done"]
    assert g["districts"][0]["groups"][0]["done_on"] == "2026-11-09"

    # A new companion means it's due again.
    import_eq(conn, (["Able, Al", "Cole, Cy"], ["Dunn, Di"]))
    assert iv.groups(conn, "eq", Q4)["done"] == 0


def test_reminders_start_near_the_end_of_the_quarter(conn):
    import_eq(conn, (["Able, Al", "Baker, Bo"], ["Dunn, Di"]))
    assert iv.due_soon(conn, "eq", date(2026, 10, 5)) == 0      # early in the quarter: no nagging
    assert iv.due_soon(conn, "eq", date(2026, 12, 1)) == 1      # within 35 days of the end
    iv.create(conn, "eq", "2026-10-20", [ids(conn)["Able, Al"], ids(conn)["Baker, Bo"]], "", "", "ty")
    assert iv.due_soon(conn, "eq", date(2026, 12, 1)) == 0


# --- interviews: notes and history ------------------------------------------

def test_notes_come_back_next_time(conn):
    import_eq(conn, (["Able, Al", "Baker, Bo"], ["Dunn, Di"]))
    p = ids(conn)
    first = iv.create(conn, "eq", "2026-08-10", [p["Able, Al"], p["Baker, Bo"]], "The Dunns need rides.",
                      "Ask about the ride rota", "ty")
    second = iv.create(conn, "eq", "2026-11-09", [p["Able, Al"], p["Baker, Bo"]], "Rides sorted.", "", "ty")

    past = iv.history(conn, [p["Able, Al"], p["Baker, Bo"]], exclude=second)
    assert [i["id"] for i in past] == [first]
    assert past[0]["follow_up"] == "Ask about the ride rota"
    assert [x["name"] for x in past[0]["ministers"]] == ["Able, Al", "Baker, Bo"]
    # Someone's history follows them to a new companion.
    assert [i["id"] for i in iv.history(conn, [p["Baker, Bo"], p["Cole, Cy"]])] == [second, first]
    assert iv.history(conn, [p["Cole, Cy"]]) == []

    iv.update(conn, first, "2026-08-11", [p["Able, Al"]], "Edited", "", "sam")
    got = iv.get(conn, first)
    assert (got["held_on"], got["notes"], got["updated_by"], got["created_by"]) == ("2026-08-11", "Edited", "sam", "ty")
    assert len(got["ministers"]) == 1
    iv.delete(conn, first)
    assert iv.get(conn, first) is None
    with pytest.raises(iv.InterviewError):
        iv.delete(conn, first)


@pytest.mark.parametrize("held_on, who, notes", [
    ("not a date", [1], ""), ("2999-01-01", [1], ""), ("2026-11-09", [], ""),
    ("2026-11-09", [9999], ""), ("2026-11-09", [1], "x" * (iv.MAX_TEXT + 1))])
def test_bad_interviews_are_refused(conn, held_on, who, notes):
    with pytest.raises(iv.InterviewError):
        iv.create(conn, "eq", held_on, who, notes, "", "ty")


def test_interview_pages(client, conn):
    import_eq(conn, (["Able, Al", "Baker, Bo"], ["Dunn, Di"]))
    conn.commit()
    p = ids(conn)
    login(client)
    page = client.get("/ministering/eq/interviews")
    assert page.status_code == 200 and "0 of 1 companionships interviewed" in page.text and "Due" in page.text
    link = f"/interviews/new?org=eq&m={p['Able, Al']}&m={p['Baker, Bo']}"
    assert f'href="{link}"' in page.text.replace("&amp;", "&")
    assert "No earlier interviews" in client.get(link).text

    r = client.post("/interviews/new", data={"org": "eq", "m": [p["Able, Al"], p["Baker, Bo"]], "held_on": TODAY,
                                              "notes": "Talked about <b>rides</b>", "follow_up": "Call the Dunns"})
    assert r.status_code == 200 and "Saved" in r.text
    interview_id = r.url.path.rsplit("/", 1)[1]
    assert "&lt;b&gt;rides&lt;/b&gt;" in r.text and "<b>rides</b>" not in r.text   # notes are escaped

    # Next time, last time's notes are on the page.
    again = client.get(link).text
    assert "Call the Dunns" in again and "Talked about" in again and "Able, Al".replace("Able, Al", "Al Able") in again
    assert "1 of 1 companionships interviewed" in client.get("/ministering/eq/interviews").text
    assert "People they minister to" in again and "Di Dunn" in again       # who to ask about during the interview

    bad = client.post(f"/interviews/{interview_id}", data={"held_on": "nope", "notes": "kept my typing", "m": [p["Able, Al"]]})
    assert bad.status_code == 400 and "Pick the date" in bad.text and "kept my typing" in bad.text
    ok = client.post(f"/interviews/{interview_id}", data={"held_on": TODAY, "notes": "Changed", "follow_up": "", "m": [p["Able, Al"]]})
    assert "Changed" in ok.text
    deleted = client.post(f"/interviews/{interview_id}/delete")
    assert deleted.status_code == 200 and client.get(f"/interviews/{interview_id}").status_code == 404
    assert client.get("/ministering/xx/interviews").status_code == 404
    assert client.get("/interviews/new").url.path == "/ministering/eq/interviews"      # nobody picked: back to the list


def test_only_leaders_see_interviews(client):
    auth.create_user("member", PW)
    login(client, "member", PW)
    for url in ("/ministering/eq/interviews", "/interviews/new?org=eq&m=1", "/interviews/1"):
        assert client.get(url).status_code == 403
    assert client.post("/interviews/new", data={"org": "eq"}).status_code == 403


def test_interviews_stay_in_their_ward(client, conn):
    import_eq(conn, (["Able, Al"], []))
    conn.commit()
    iv.create(conn, "eq", "2026-11-09", [ids(conn)["Able, Al"]], "Private notes", "", "ty")
    conn.commit()
    with db.ward_connect(1) as c:
        interview_id = c.execute("SELECT id FROM interviews").fetchone()[0]
    login(client)
    client.post("/admin/wards", data={"name": "Other Ward"})
    with db.connect() as c:
        other = c.execute("SELECT id FROM wards WHERE name = 'Other Ward'").fetchone()[0]
    client.post(f"/admin/wards/{other}/switch")
    assert client.get(f"/interviews/{interview_id}").status_code == 404


def test_old_welcome_table_is_upgraded(tmp_path):
    """The first version had an introduced_at column; it becomes contacted_at, with old ticks cleared."""
    import sqlite3
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE welcome (person_id INTEGER PRIMARY KEY, added_at TEXT, introduced_at TEXT, "
                 "calling_at TEXT, dismissed_at TEXT)")
    conn.execute("INSERT INTO welcome VALUES (1, 'x', '2026-11-01', '2026-11-02', NULL)")
    db._migrate_columns(conn)
    assert conn.execute("SELECT contacted_at, calling_at FROM welcome").fetchone()[:] == (None, "2026-11-02")

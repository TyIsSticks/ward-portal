"""Lesson planning: conference talks, the plan, teachers. Made-up talks and names only."""
import urllib.error
from datetime import date

import pytest

from app import db, lessons as les
from app.reports import conference as gc
from app.reports.birthdays import ReportError
from tests.conftest import login

# --- calendar ---------------------------------------------------------------


def test_conference_sundays_and_lesson_weeks():
    assert les.conference_sunday(2026, 10) == date(2026, 10, 4)
    assert les.conference_sunday(2027, 4) == date(2027, 4, 4)
    assert les.conference_sunday(2023, 10) == date(2023, 10, 1)   # the month starts on a Sunday
    weeks = les.sundays(2026, 10)
    assert weeks[0] == date(2026, 10, 11) and weeks[-1] == date(2027, 3, 28) and len(weeks) == 25
    assert all(d.weekday() == 6 for d in weeks)


def test_conference_for_a_day():
    assert les.conference_for(date(2026, 10, 3)) == (2026, 4)    # the Saturday session hasn't ended
    assert les.conference_for(date(2026, 10, 4)) == (2026, 10)
    assert les.conference_for(date(2027, 1, 15)) == (2026, 10)
    assert les.conference_for(date(2027, 4, 1)) == (2026, 10)
    assert les.parse_conf("2027-04") == (2027, 4)
    assert les.parse_conf("2027-05", today=date(2027, 1, 1)) == (2026, 10)   # not a conference month
    assert les.parse_conf("junk", today=date(2027, 1, 1)) == (2026, 10)


# --- reading conference talks ------------------------------------------------

LISTING = """
<header><h1 class="title">October 2030 general conference</h1></header>
<nav class="manifest"><ul class="doc-map">
<li data-content-type="general-conference-session"><h2 class="label"><p class="title">Saturday Morning Session</p></h2>
<ul class="doc-map"><li><a href="/study/general-conference/2030/10/saturday-morning-session?lang=eng" class="list-tile">
  <p class="title">Saturday Morning Session</p><p class="description">The session.</p></a></li>
<li data-content-type="general-conference-business"><a href="/study/general-conference/2030/10/11apple?lang=eng">
  <p class="primaryMeta">Ann B. Apple</p><p class="title">Sustaining of General Authorities</p><p class="description">Votes.</p></a></li>
<li data-content-type="general-conference-talk"><a href="/study/general-conference/2030/10/12birch?lang=eng">
  <p class="primaryMeta">Carl&nbsp;D. Birch</p><p class="title">“Faith &amp; Hope”</p><p class="description">Elder Birch teaches.</p></a></li>
</ul></li>
<li data-content-type="general-conference-session"><h2 class="label"><p class="title">Sunday Morning Session</p></h2>
<ul class="doc-map">
<li data-content-type="general-conference-talk"><a href="/study/general-conference/2030/10/41cedar?lang=eng">
  <p class="primaryMeta">Dee Cedar</p><p class="title">Light</p><p class="description">Sister Cedar teaches.</p></a></li>
<li data-content-type="general-conference-talk"><a href="/study/general-conference/2030/10/42birch?lang=eng">
  <p class="primaryMeta">Carl D. Birch</p><p class="title">Closing Remarks</p><p class="description">Thanks.</p></a></li>
</ul></li></ul></nav>
"""

CALLINGS = {
    "/general-conference/2030/10/11apple": "Second Counselor in the First Presidency",
    "/general-conference/2030/10/12birch": "Of the Quorum of the Twelve Apostles",
    "/general-conference/2030/10/41cedar": "Primary General President",
    "/general-conference/2030/10/42birch": "Of the Quorum of the Twelve Apostles",
}


def fake_site(uri, timeout=15):
    if uri == "/general-conference/2030/10":
        return {"content": {"body": LISTING}}
    if uri in CALLINGS:
        return {"content": {"body": f'<p class="author-name">By X</p><p class="author-role" data-aid="1">{CALLINGS[uri]}</p>'}}
    raise urllib.error.HTTPError(uri, 404, "Not Found", {}, None)


def test_parse_listing():
    talks = gc.parse_listing(LISTING)
    assert [(t.session, t.speaker, t.title, t.kind) for t in talks] == [
        ("Saturday Morning Session", "Ann B. Apple", "Sustaining of General Authorities", "business"),
        ("Saturday Morning Session", "Carl D. Birch", "“Faith & Hope”", "talk"),
        ("Sunday Morning Session", "Dee Cedar", "Light", "talk"),
        ("Sunday Morning Session", "Carl D. Birch", "Closing Remarks", "talk"),
    ]
    assert talks[1].uri == "/study/general-conference/2030/10/12birch"
    assert talks[1].url == "https://www.churchofjesuschrist.org/study/general-conference/2030/10/12birch?lang=eng"


def test_fetch_uses_callings(monkeypatch):
    monkeypatch.setattr(gc, "_get", fake_site)
    conf = gc.fetch(2030, 10)
    assert conf.name == "October 2030"
    assert [(t.speaker, t.required) for t in conf.talks] == [
        ("Ann B. Apple", True), ("Carl D. Birch", True), ("Dee Cedar", False), ("Carl D. Birch", True)]
    assert conf.talks[2].calling == "Primary General President"


def test_fetch_errors_suggest_the_pdf(monkeypatch):
    monkeypatch.setattr(gc, "_get", fake_site)
    with pytest.raises(ReportError, match="isn't on the Church website yet"):
        gc.fetch(2031, 4)

    def offline(uri, timeout=15):
        raise urllib.error.URLError("no network")
    monkeypatch.setattr(gc, "_get", offline)
    with pytest.raises(ReportError, match="Upload the conference PDF"):
        gc.fetch(2030, 10)


def test_pdf_tiles_become_talks():
    link = lambda page, top, slug: {"page": page, "top": top, "bottom": top + 86,
                                    "uri": f"https://www.churchofjesuschrist.org/study/general-conference/2030/10/{slug}?lang=eng"}
    line = lambda page, top, text, size: {"page": page, "top": top, "text": text, "size": size}
    lines = [line(0, 70, "October 2030 general conference", 25.2),
             line(0, 120, "Saturday Morning Session", 19.2),
             line(0, 160, "Saturday Morning Session", 10.8), line(0, 178, "The session.", 8.4),
             line(0, 247, "Carl D. Birch", 7.8), line(0, 260, "A Long Title That", 10.8),
             line(0, 273, "Wraps", 10.8), line(0, 290, "Elder Birch teaches", 8.4), line(0, 300, "a lot.", 8.4),
             line(1, 30, "Dee Cedar", 7.8), line(1, 43, "Light", 10.8), line(1, 61, "Sister Cedar teaches.", 8.4)]
    links = [link(0, 158, "saturday-morning-session"), link(0, 246, "12birch"), link(1, 28, "41cedar")]
    heading, talks = gc.group_tiles(lines, links)
    assert heading == "October 2030 general conference"
    assert [(t.session, t.speaker, t.title, t.summary, t.uri) for t in talks] == [
        ("Saturday Morning Session", "Carl D. Birch", "A Long Title That Wraps", "Elder Birch teaches a lot.",
         "/study/general-conference/2030/10/12birch"),
        ("Saturday Morning Session", "Dee Cedar", "Light", "Sister Cedar teaches.",
         "/study/general-conference/2030/10/41cedar"),
    ]


def test_store_conference_keeps_ids_and_callings(client, monkeypatch):
    monkeypatch.setattr(gc, "_get", fake_site)
    with db.ward_connect(1) as conn:
        cid = les.store_conference(conn, gc.fetch(2030, 10), "website", "admin")
        before = {t["uri"]: t for t in les.talks(conn, cid)}
        assert les.required_speakers(list(before.values())) == ["Carl D. Birch"]  # sustaining isn't a lesson

        # A PDF has no callings and one fewer talk: ids stay, callings and ticks stay, the missing talk goes.
        pdf = gc.parse_listing(LISTING)[1:]
        gc.guess_required(pdf, les.known_required(conn))
        assert [t.required for t in pdf] == [True, False, True]   # learned from the website load
        assert les.store_conference(conn, gc.Conference(2030, 10, pdf), "pdf", "admin") == cid
        after = {t["uri"]: t for t in les.talks(conn, cid)}
        assert set(after) == set(before) - {"/study/general-conference/2030/10/11apple"}
        for uri, t in after.items():
            assert t["id"] == before[uri]["id"] and t["calling"] == before[uri]["calling"]
        assert les.get_conference(conn, 2030, 10)["source"] == "pdf"

        les.set_required(conn, cid, {"Dee Cedar"})
        assert les.required_speakers(les.talks(conn, cid)) == ["Dee Cedar"]


# --- pages ------------------------------------------------------------------

def test_talks_page_fetch_and_ticks(client, monkeypatch):
    monkeypatch.setattr(gc, "_get", fake_site)
    login(client)
    page = client.get("/lessons/talks?conf=2030-10").text
    assert "Not loaded yet" in page and "Get talks from the Church website" in page
    r = client.post("/lessons/talks/fetch", data={"conf": "2030-10"})
    assert "Loaded 4 talks for October 2030 from the Church website" in r.text
    assert "1 speakers are First Presidency or Twelve" in r.text
    assert "Not a lesson" in r.text and "Primary General President" in r.text

    client.post("/lessons/talks/required", data={"conf": "2030-10", "speaker": ["Dee Cedar", "Carl D. Birch"]})
    with db.ward_connect(1) as conn:
        cid = les.get_conference(conn, 2030, 10)["id"]
        assert les.required_speakers(les.talks(conn, cid)) == ["Carl D. Birch", "Dee Cedar"]

    assert "on the Church website yet" in client.post("/lessons/talks/fetch", data={"conf": "2031-04"}).text


def test_conference_pdf_upload_is_for_leaders(client, monkeypatch):
    from app import auth, main
    monkeypatch.setattr(main, "detect", lambda data: "conference")
    monkeypatch.setattr(gc, "parse_pdf", lambda data: gc.Conference(2030, 10, gc.parse_listing(LISTING)))
    auth.create_user("mem", "a long enough password", role="member")
    login(client, "mem", "a long enough password")
    r = client.post("/upload", files={"file": ("c.pdf", b"%PDF", "application/pdf")})
    assert "Only leaders can load general conference talks" in r.text
    assert client.get("/lessons/talks").status_code == 403

    client.post("/logout")
    login(client)
    r = client.post("/upload", files={"file": ("c.pdf", b"%PDF", "application/pdf")})
    assert "Loaded 4 talks for October 2030 from the PDF" in r.text
    assert "check the ticked speakers" in r.text

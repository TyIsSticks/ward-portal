def _feed_path(client):
    from app import db
    with db.connect() as conn:
        return f"/feed/{db.get_setting(conn, 'feed_token')}/birthdays.ics"


def test_pages_require_login(client):
    for path in ["/", "/birthdays"]:
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"


def test_login_and_dashboard(client):
    r = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert "Invalid" in r.text
    r = client.post("/login", data={"username": "admin", "password": "correct horse battery"})
    assert r.status_code == 200 and "At a glance" in r.text


def test_feed_requires_correct_token(client):
    assert client.get("/feed/nope/birthdays.ics").status_code == 404
    r = client.get(_feed_path(client))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")


def test_upload_rejects_non_pdf(client):
    client.post("/login", data={"username": "admin", "password": "correct horse battery"})
    r = client.post("/upload", files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert "be opened as a PDF" in r.text


def test_flagged_birthdays_on_dashboard(client):
    from app import db
    from app.reports.birthdays import Birthday, sync
    with db.connect() as conn:
        sync(conn, [Birthday(1, 1, "Gone, Gary"), Birthday(2, 2, "Here, Hal")])
        sync(conn, [Birthday(2, 2, "Here, Hal")])
        gary = conn.execute("SELECT id FROM birthdays WHERE name = 'Gone, Gary'").fetchone()[0]
    client.post("/login", data={"username": "admin", "password": "correct horse battery"})
    assert "1 birthday may have moved out" in client.get("/").text  # dashboard to-do
    page = client.get("/birthdays").text
    assert "Possibly moved out (1)" in page and "Gary Gone" in page
    assert "Gary Gone" in client.get(_feed_path(client)).text  # still on the calendar
    client.post(f"/birthdays/{gary}/resolve", data={"action": "remove"})
    assert "Possibly moved out" not in client.get("/birthdays").text
    assert "Gary Gone" not in client.get(_feed_path(client)).text


def test_birthday_page_month_calendar(client):
    from app import db
    from app.reports.birthdays import Birthday, sync
    with db.connect() as conn:
        sync(conn, [Birthday(2, 29, "Leap, Lou"), Birthday(3, 1, "March, May")])
    client.post("/login", data={"username": "admin", "password": "correct horse battery"})
    page = client.get("/birthdays?month=2027-02").text   # not a leap year: Feb 29 shows on the 28th
    assert "February" in page and "2027" in page and "Lou Leap" in page and "May March" not in page
    assert "Lou Leap" in client.get("/birthdays?month=2028-02").text
    assert client.get("/birthdays?month=nonsense").status_code == 200
    assert "Upload an LCR report" in client.get("/upload").text

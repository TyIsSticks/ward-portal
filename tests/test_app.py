def _feed_path(client):
    from app import db
    with db.connect() as conn:
        token = conn.execute("SELECT feed_token FROM wards WHERE id = 1").fetchone()[0]
    return f"/feed/{token}/birthdays.ics"


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
    with db.ward_connect(1) as conn:
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
    with db.ward_connect(1) as conn:
        sync(conn, [Birthday(2, 29, "Leap, Lou"), Birthday(3, 1, "March, May")])
    client.post("/login", data={"username": "admin", "password": "correct horse battery"})
    page = client.get("/birthdays?month=2027-02").text   # not a leap year: Feb 29 shows on the 28th
    assert "February" in page and "2027" in page and "Lou Leap" in page and "May March" not in page
    assert "Lou Leap" in client.get("/birthdays?month=2028-02").text
    assert client.get("/birthdays?month=nonsense").status_code == 200
    assert "Upload an LCR report" in client.get("/upload").text


def test_todays_birthdays_on_dashboard_todo(client):
    from datetime import date, timedelta
    from app import db
    from app.reports.birthdays import Birthday, sync
    today, tomorrow = date.today(), date.today() + timedelta(days=1)
    with db.ward_connect(1) as conn:
        sync(conn, [Birthday(today.month, today.day, "Today, Tia"), Birthday(today.month, today.day, "Aged, Abe"),
                    Birthday(tomorrow.month, tomorrow.day, "Later, Lin")])
        conn.execute("INSERT INTO people (name, gender, birth_year, birth_month, birth_day) VALUES (?, 'M', ?, ?, ?)",
                     ("Aged, Abe", today.year - 40, today.month, today.day))
    client.post("/login", data={"username": "admin", "password": "correct horse battery"})
    page = client.get("/").text
    assert "Tell Tia Today happy birthday!" in page
    assert "Tell Abe Aged happy birthday! They turn 40." in page
    assert "Tell Lin Later" not in page

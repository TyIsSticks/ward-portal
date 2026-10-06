import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app import auth, config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "portal.db")
    from app.main import app
    with TestClient(app) as c:
        auth.create_user("admin", "correct horse battery", is_admin=True)
        yield c


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
    assert r.status_code == 200 and "Calendar feed" in r.text


def test_feed_requires_correct_token(client):
    assert client.get("/feed/nope/birthdays.ics").status_code == 404
    r = client.get(_feed_path(client))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")


def test_upload_rejects_non_pdf(client):
    client.post("/login", data={"username": "admin", "password": "correct horse battery"})
    r = client.post("/birthdays/upload", files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert "be opened as a PDF" in r.text

import pytest
from fastapi.testclient import TestClient

ADMIN_PW = "correct horse battery"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app import auth, config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "portal.db")
    monkeypatch.setattr(auth, "_failures", {})
    from app.main import app
    with TestClient(app) as c:
        auth.create_user("admin", ADMIN_PW, is_admin=True)
        yield c


def login(client, username="admin", password=ADMIN_PW):
    r = client.post("/login", data={"username": username, "password": password})
    assert r.status_code == 200 and "Sign in</h1>" not in r.text, "login failed"
    return r

import re
import sqlite3

from fastapi.testclient import TestClient

from app import auth, db
from tests.conftest import ADMIN_PW, login

PW = "a long enough password"


def _make_link(client, path="/admin/invites", **data) -> str:
    r = client.post(path, data=data)
    m = re.search(r'value="https?://[^"]+(/invite/[^"]+)"', r.text)
    assert m, "no link on page"
    return m.group(1)


def _redeem(client, link, username="newbie", password=PW, confirm=None):
    return client.post(link, data={"username": username, "password": password,
                                   "confirm": confirm or password})


def test_members_cannot_manage_users(client):
    auth.create_user("member", PW)
    login(client, "member", PW)
    assert client.get("/admin/users").status_code == 403
    assert client.post("/admin/invites", data={}).status_code == 403
    assert "/admin/users" not in client.get("/").text


def test_invite_flow(client):
    login(client)
    link = _make_link(client, note="Sister Test", role="member")
    assert "Sister Test" in client.get("/admin/users").text  # listed as pending
    client.post("/logout")

    guest = TestClient(client.app)
    assert "Create your account" in guest.get(link).text
    r = _redeem(guest, link)
    assert r.url.path == "/" and "Welcome" in r.text
    assert "Calendar feed" in guest.get("/").text  # signed in as the new member
    assert "/admin/users" not in guest.get("/").text

    # Single use.
    assert TestClient(client.app).get(link).status_code == 404
    assert _redeem(TestClient(client.app), link, username="other").status_code == 404


def test_admin_invite_grants_admin(client):
    login(client)
    link = _make_link(client, role="admin")
    guest = TestClient(client.app)
    _redeem(guest, link, username="second-admin")
    assert guest.get("/admin/users").status_code == 200


def test_bad_input_keeps_link_usable(client):
    login(client)
    link = _make_link(client)
    guest = TestClient(client.app)
    assert "taken" in _redeem(guest, link, username="admin").text
    assert "match" in _redeem(guest, link, confirm="something else entirely").text
    assert "at least 10" in _redeem(guest, link, password="short").text
    assert "3–32" in _redeem(guest, link, username="no spaces allowed").text
    assert _redeem(guest, link).url.path == "/"


def test_revoked_invite_stops_working(client):
    login(client)
    link = _make_link(client)
    with db.connect() as conn:
        invite_id = conn.execute("SELECT id FROM invites").fetchone()[0]
    client.post(f"/admin/invites/{invite_id}/revoke")
    assert TestClient(client.app).get(link).status_code == 404


def test_reset_link_sets_password_and_signs_out_old_sessions(client):
    member_id = auth.create_user("member", PW)
    old_session = TestClient(client.app)
    login(old_session, "member", PW)

    login(client)
    link = _make_link(client, f"/admin/users/{member_id}/reset")
    guest = TestClient(client.app)
    assert "member" in guest.get(link).text
    guest.post(link, data={"password": "brand new password", "confirm": "brand new password"})

    assert old_session.get("/", follow_redirects=False).status_code == 303  # signed out
    assert auth.authenticate("member", "brand new password")
    assert not auth.authenticate("member", PW)


def test_only_newest_reset_link_works(client):
    member_id = auth.create_user("member", PW)
    login(client)
    first = _make_link(client, f"/admin/users/{member_id}/reset")
    second = _make_link(client, f"/admin/users/{member_id}/reset")
    assert TestClient(client.app).get(first).status_code == 404
    assert TestClient(client.app).get(second).status_code == 200


def test_role_change_and_delete(client):
    member_id = auth.create_user("member", PW)
    member = TestClient(client.app)
    login(member, "member", PW)
    login(client)

    client.post(f"/admin/users/{member_id}/role", data={"role": "admin"})
    assert member.get("/admin/users").status_code == 200
    client.post(f"/admin/users/{member_id}/role", data={"role": "member"})
    assert member.get("/admin/users").status_code == 403

    client.post(f"/admin/users/{member_id}/delete")
    assert member.get("/", follow_redirects=False).status_code == 303


def test_admin_cannot_demote_or_delete_self(client):
    login(client)
    with db.connect() as conn:
        admin_id = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    assert "own account" in client.post(f"/admin/users/{admin_id}/role", data={"role": "member"}).text
    assert "own account" in client.post(f"/admin/users/{admin_id}/delete").text
    assert client.get("/admin/users").status_code == 200


def test_change_own_password(client):
    other = TestClient(client.app)
    login(other)
    login(client)
    r = client.post("/account/password", data={"current": "wrong", "password": PW, "confirm": PW})
    assert "current password was wrong" in r.text
    r = client.post("/account/password", data={"current": ADMIN_PW, "password": PW, "confirm": PW})
    assert "Password changed" in r.text
    assert client.get("/account").status_code == 200  # this session stays signed in
    assert other.get("/", follow_redirects=False).status_code == 303  # others signed out
    assert auth.authenticate("admin", PW)


def test_migrates_database_from_first_version(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "portal.db")
    old = sqlite3.connect(tmp_path / "portal.db")
    old.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE birthdays (id INTEGER PRIMARY KEY, name TEXT NOT NULL, month INTEGER NOT NULL,
            day INTEGER NOT NULL, UNIQUE (name, month, day));
        INSERT INTO birthdays (name, month, day) VALUES ('Doe, Jane', 1, 3);
    """)
    old.execute("INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
                ("ty", auth.hash_password(PW)))
    old.commit()
    old.close()

    db.init()
    with db.connect() as conn:
        assert conn.execute("SELECT session_version FROM users").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM birthdays").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM invites").fetchone()[0] == 0
    assert auth.authenticate("ty", PW)

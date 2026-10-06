"""Multiple wards: isolation of member data, and who can manage what. Made-up names only."""
import re

from fastapi.testclient import TestClient

from app import auth, db, ministering as m, wards
from app.reports import directory
from app.reports.birthdays import Birthday, sync
from tests.conftest import login

PW = "a long enough password"


def _new_link(page: str) -> str:
    match = re.search(r'value="https?://[^"]+(/invite/[^"]+)"', page)
    assert match, "no invite link on page"
    return match.group(1)


def _second_ward(client) -> tuple[int, TestClient]:
    """Admin creates a ward and invites its ward admin; returns (ward id, ward admin's client)."""
    login(client)
    r = client.post("/admin/wards", data={"name": "Maple Creek 3rd Ward"})
    ward_id = int(re.search(r"ward-(\d+)", str(r.url)).group(1))
    page = client.post(f"/admin/wards/{ward_id}/invite", data={"note": "Friend"}).text
    friend = TestClient(client.app)
    friend.post(_new_link(page), data={"username": "friend", "password": PW, "confirm": PW})
    return ward_id, friend


def _feed(ward_id: int) -> str:
    with db.connect() as conn:
        return f"/feed/{wards.get(conn, ward_id)['feed_token']}/birthdays.ics"


def test_wards_keep_their_own_data(client):
    with db.ward_connect(1) as conn:
        sync(conn, [Birthday(1, 3, "First, Ann")])
    ward_id, friend = _second_ward(client)
    with db.ward_connect(ward_id) as conn:
        sync(conn, [Birthday(5, 6, "Second, Sam")])

    page = friend.get("/birthdays").text
    assert "Maple Creek 3rd Ward" in page and "1 on the calendar" in page
    assert "Ann First" not in friend.get("/birthdays?month=2027-01").text
    assert "Sam Second" in friend.get("/birthdays?month=2027-05").text

    # Each calendar link serves only its own ward.
    assert "Ann First" in client.get(_feed(1)).text and "Sam Second" not in client.get(_feed(1)).text
    assert "Sam Second" in client.get(_feed(ward_id)).text and "Ann First" not in client.get(_feed(ward_id)).text
    assert _feed(1) != _feed(ward_id)


def test_ministering_is_per_ward(client, monkeypatch):
    import app.main as main
    ward_id, friend = _second_ward(client)
    roster = [directory.Member("Able, Al", "M", None, 2000, 1, 1), directory.Member("Baker, Bo", "M", None, 2000, 2, 2)]
    monkeypatch.setattr(main, "detect", lambda data: "directory")
    monkeypatch.setattr(main.directory, "parse_pdf", lambda data: roster)
    friend.post("/upload", files={"file": ("d.pdf", b"%PDF", "application/pdf")})
    friend.post("/ministering/eq/layouts", data={"name": "Friend draft", "start": "scratch"})

    with db.ward_connect(ward_id) as conn:
        assert conn.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 2
        assert [l["name"] for l in m.list_layouts(conn, "eq")] == ["Friend draft"]
    with db.ward_connect(1) as conn:
        assert conn.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 0
        assert m.list_layouts(conn, "eq") == []
    assert "Friend draft" not in client.get("/ministering/eq").text  # admin is still in ward 1


def test_admin_can_switch_wards(client):
    ward_id, _ = _second_ward(client)
    assert "Maple Creek 3rd Ward" not in client.get("/birthdays").text.split("<main")[1]
    client.post(f"/admin/wards/{ward_id}/switch")
    page = client.get("/admin/users").text.split("<main")[1]
    assert "Maple Creek 3rd Ward" in page and "friend" in page and "<td>admin" not in page


def test_ward_admin_scope(client):
    ward_id, friend = _second_ward(client)
    assert friend.get("/admin/wards").status_code == 403
    users_page = friend.get("/admin/users").text
    assert "friend" in users_page and "admin" not in re.findall(r"<td>(\w+)", users_page)
    assert 'value="admin"' not in users_page  # can't hand out the site-wide role

    # Inviting into their own ward; an "admin" request falls back to member.
    page = friend.post("/admin/invites", data={"note": "Sister X", "role": "admin"}).text
    newbie = TestClient(client.app)
    newbie.post(_new_link(page), data={"username": "newbie", "password": PW, "confirm": PW})
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = 'newbie'").fetchone()
        assert row["ward_id"] == ward_id and auth.role_of(row) == "member"
        admin_id = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]

    # Can't touch accounts in another ward (the admin lives in ward 1).
    friend.post(f"/admin/users/{admin_id}/role", data={"role": "member"})
    friend.post(f"/admin/users/{admin_id}/delete")
    assert auth.authenticate("admin", "correct horse battery")
    with db.connect() as conn:
        assert conn.execute("SELECT is_admin FROM users WHERE id = ?", (admin_id,)).fetchone()[0] == 1


def test_leaders_and_members_cannot_manage_accounts(client):
    ward_id, friend = _second_ward(client)
    auth.create_user("leader2", PW, role="leader", ward_id=ward_id)
    leader = TestClient(client.app)
    login(leader, "leader2", PW)
    assert leader.get("/ministering/eq").status_code == 200
    assert leader.get("/admin/users").status_code == 403
    assert leader.post("/feed/regenerate").status_code == 403
    assert "Maple Creek 3rd Ward" in leader.get("/").text


def test_new_install_gets_a_first_ward(client):
    with db.connect() as conn:
        rows = wards.list_wards(conn)
    assert len(rows) == 1 and rows[0]["feed_token"]
    with db.connect() as conn:
        assert conn.execute("SELECT ward_id FROM users WHERE username = 'admin'").fetchone()[0] == rows[0]["id"]

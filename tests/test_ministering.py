"""Ministering data layer and routes. Made-up names only."""
import re

import pytest
from fastapi.testclient import TestClient

from app import auth, db, ministering as m
from app.reports import directory
from app.reports import ministering as report
from tests.conftest import login

PW = "a long enough password"


def member(name, gender="M", month=1, day=1, year=2000):
    return directory.Member(name=name, gender=gender, age=None, birth_year=year, birth_month=month, birth_day=day)


ROSTER = [member("Able, Al"), member("Baker, Bo"), member("Cole, Cy"), member("Dunn, Di", "F"),
          member("Eads, Em", "F", 2, 29, 2004), member("Ford, Fay", "F")]


def ids(conn):
    return {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM people")}


def report_of(*groups, district="North"):
    return [report.District(name=district, supervisor="Okey, Austin", companionships=[
        report.Companionship(ministers=list(ms), assigned=[report.Assigned(n) for n in asg]) for ms, asg in groups])]


@pytest.fixture
def conn(client):
    with db.connect() as c:
        m.sync_people(c, ROSTER)
        c.commit()  # don't hold a write lock while the app's own connections run
        yield c


# --- roster -----------------------------------------------------------------

def test_sync_people_tracks_moves_and_tags_move_ins(client):
    with db.connect() as conn:
        first = m.sync_people(conn, ROSTER)
        assert first["first_import"] and first["moved_in"] == []  # first upload isn't "moving in"

        result = m.sync_people(conn, ROSTER[1:] + [member("New, Nia", "F")])
        assert result["moved_in"] == ["New, Nia"] and result["moved_out"] == ["Able, Al"]
        people = m.people(conn)
        nia = next(p for p in people.values() if p["name"] == "New, Nia")
        assert nia["tags"] == ["New move-in"]
        assert all(p["name"] != "Able, Al" for p in people.values())  # inactive hidden by default

        back = m.sync_people(conn, ROSTER + [member("New, Nia", "F")])
        assert back["returned"] == ["Able, Al"]


def test_directory_feeds_birthdays():
    bdays = m.directory_birthdays([member("Eads, Em", "F", 2, 29), member("Nobirth, X", month=None, day=None)])
    assert [(b.name, b.month, b.day) for b in bdays] == [("Eads, Em", 2, 29)]


# --- import -----------------------------------------------------------------

def test_import_matches_names_and_flags_unknowns(conn):
    payload = m.match_names(conn, report_of((["Able, Al", "Baker, Bo"], ["Dunn, Di", "Ford, Faye"])))
    assert [u["name"] for u in payload["unmatched"]] == ["Ford, Faye"]
    assert payload["unmatched"][0]["suggestions"][0]["name"] == "Ford, Fay"

    layout_id = m.finish_import(conn, payload, {"Ford, Faye": ids(conn)["Ford, Fay"]}, "admin")
    p = ids(conn)
    assert m.structure(conn, layout_id) == [{"name": "North", "supervisor": "Okey, Austin", "groups": [
        {"ministers": [p["Able, Al"], p["Baker, Bo"]], "assigned": [p["Dunn, Di"], p["Ford, Fay"]]}]}]
    assert m.current_import(conn)["id"] == layout_id


def test_new_import_becomes_current_and_old_one_becomes_history(conn):
    p = ids(conn)
    old = m.finish_import(conn, m.match_names(conn, report_of((["Able, Al", "Baker, Bo"], ["Dunn, Di"]))), {}, "a")
    new = m.finish_import(conn, m.match_names(conn, report_of((["Able, Al", "Cole, Cy"], ["Eads, Em"]))), {}, "a")
    assert m.current_import(conn)["id"] == new
    hist = m.history(conn, exclude_layout=new)
    a, b = sorted((p["Able, Al"], p["Baker, Bo"]))
    assert f"{a}-{b}" in hist["companions"]
    assert f"{p['Able, Al']}-{p['Dunn, Di']}" in hist["ministered"]
    # The current import itself is not "history".
    assert not any(str(p["Cole, Cy"]) in k.split("-") for k in hist["companions"])
    assert old != new


# --- layouts ----------------------------------------------------------------

def test_create_layout_variants(conn):
    with pytest.raises(m.LayoutError):
        m.create_layout(conn, "x", "current", "a")  # nothing imported yet
    scratch = m.create_layout(conn, "", "scratch", "a")
    assert m.structure(conn, scratch) == [{"name": "District 1", "supervisor": "", "groups": []}]

    imported = m.finish_import(conn, m.match_names(conn, report_of((["Able, Al"], ["Dunn, Di"]))), {}, "a")
    current = m.create_layout(conn, "From current", "current", "a")
    assert m.structure(conn, current) == m.structure(conn, imported)
    scratch2 = m.create_layout(conn, "Scratch", "scratch", "a")
    assert m.structure(conn, scratch2) == [{"name": "North", "supervisor": "Okey, Austin", "groups": []}]
    copy = m.create_layout(conn, "Copy", "copy", "a", copy_from=current)
    assert m.structure(conn, copy) == m.structure(conn, current)


def test_save_structure_checks_version_readonly_and_people(conn):
    p = ids(conn)
    layout_id = m.create_layout(conn, "Draft", "scratch", "a")
    structure = [{"name": "N", "supervisor": "", "groups": [{"ministers": [p["Able, Al"]], "assigned": [p["Cole, Cy"]]}]}]
    v2 = m.save_structure(conn, layout_id, structure, expected_version=1)
    assert v2 == 2 and m.structure(conn, layout_id) == structure
    with pytest.raises(m.VersionConflict):
        m.save_structure(conn, layout_id, structure, expected_version=1)
    with pytest.raises(m.LayoutError):
        m.save_structure(conn, layout_id, [{"name": "N", "groups": [{"ministers": [99999], "assigned": []}]}], 2)

    m.update_meta(conn, layout_id, status="approved")
    with pytest.raises(m.LayoutError):
        m.save_structure(conn, layout_id, structure, 2)  # approved layouts are locked
    imported = m.finish_import(conn, m.match_names(conn, report_of((["Able, Al"], []))), {}, "a")
    with pytest.raises(m.LayoutError):
        m.save_structure(conn, imported, structure, 1)
    with pytest.raises(m.LayoutError):
        m.delete_layout(conn, imported)


def test_tags_only_accept_presets(conn):
    pid = ids(conn)["Able, Al"]
    assert m.set_tags(conn, pid, ["Needs extra care", "made up", "New move-in"]) == ["New move-in", "Needs extra care"]


# --- changes for LCR --------------------------------------------------------

def test_changes_match_groups_by_overlap():
    base = [{"name": "N", "groups": [
        {"ministers": [1, 2], "assigned": [10, 11]},
        {"ministers": [3, 4], "assigned": [12]},
        {"ministers": [5, 6], "assigned": [13]}]}]
    target = [{"name": "N", "groups": [
        {"ministers": [1, 7], "assigned": [10, 12]},     # companion swap + reassignment
        {"ministers": [5, 6], "assigned": [13]}]},        # unchanged
        {"name": "S", "groups": [
        {"ministers": [3, 8], "assigned": [11]},          # moved district, new companion
        {"ministers": [9], "assigned": [14]}]}]           # brand new
    diff = m.changes(base, target)
    by_ministers = {frozenset(c["ministers"]): c for c in diff["changed"]}
    first = by_ministers[frozenset({1, 2})]
    assert first["add_ministers"] == {7} and first["remove_ministers"] == {2}
    assert first["add_assigned"] == {12} and first["remove_assigned"] == {11}
    moved = by_ministers[frozenset({3, 4})]
    assert moved["new_district"] == "S" and moved["add_ministers"] == {8} and moved["add_assigned"] == {11}
    assert frozenset({5, 6}) not in by_ministers
    assert [c["ministers"] for c in diff["created"]] == [{9}]
    assert diff["removed"] == []
    assert [d["name"] for d in diff["new_districts"]] == ["S"]
    assert diff["was_assigned_to"][12]["ministers"] == {3, 4}


def test_changes_lists_removed_groups():
    base = [{"name": "N", "groups": [{"ministers": [1, 2], "assigned": [10]}]}]
    diff = m.changes(base, [{"name": "N", "groups": []}])
    assert [c["ministers"] for c in diff["removed"]] == [{1, 2}]


# --- routes -----------------------------------------------------------------

def _leader(client):
    auth.create_user("leader", PW, role="leader")
    login(client, "leader", PW)


def test_members_cannot_see_ministering(client):
    auth.create_user("member", PW)
    login(client, "member", PW)
    assert "/ministering" not in client.get("/").text
    for path in ["/ministering", "/ministering/people", "/api/ministering/layouts/1"]:
        assert client.get(path).status_code == 403


def test_board_flow(client, conn):
    _leader(client)
    p = ids(conn)
    m.finish_import(conn, m.match_names(conn, report_of((["Able, Al", "Baker, Bo"], ["Dunn, Di"]))), {}, "a")
    conn.commit()

    r = client.post("/ministering/layouts", data={"name": "Fall", "start": "current"})
    layout_id = int(re.search(r"/ministering/layouts/(\d+)$", str(r.url)).group(1))
    assert "Fall" in r.text and 'id="state"' in r.text

    state = client.get(f"/api/ministering/layouts/{layout_id}").json()
    assert state["layout"]["readonly"] is False
    districts = state["districts"]
    districts[0]["groups"][0]["assigned"].append(p["Eads, Em"])

    url = f"/api/ministering/layouts/{layout_id}/structure"
    assert client.put(url, content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    r = client.put(url, json={"version": state["layout"]["version"], "districts": districts})
    assert r.status_code == 200
    assert client.put(url, json={"version": state["layout"]["version"], "districts": districts}).status_code == 409

    changes = client.get(f"/ministering/layouts/{layout_id}/changes").text
    assert "Assign <strong>Em Eads</strong>" in changes

    r = client.patch(f"/api/ministering/layouts/{layout_id}", json={"status": "approved"})
    assert r.json() == {"name": "Fall", "status": "approved", "readonly": True}
    r = client.put(url, json={"version": r.json() and 2, "districts": districts})
    assert r.status_code == 400

    r = client.put(f"/api/ministering/people/{p['Cole, Cy']}/tags", json={"tags": ["Needs extra care"]})
    assert r.json() == {"tags": ["Needs extra care"]}
    assert "Needs extra care" in client.get("/ministering/people").text


def test_upload_routes_reports_by_type(client, monkeypatch):
    import app.main as main
    _leader(client)
    monkeypatch.setattr(main, "detect", lambda data: "directory")
    monkeypatch.setattr(main.directory, "parse_pdf", lambda data: ROSTER)
    r = client.post("/upload", files={"file": ("d.pdf", b"%PDF", "application/pdf")})
    assert "Ward roster" in r.text and "first directory upload" in r.text
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM birthdays").fetchone()[0] == len(ROSTER)

    monkeypatch.setattr(main, "detect", lambda data: "ministering")
    monkeypatch.setattr(main.ministering_report, "parse_pdf",
                        lambda data: report_of((["Able, Al", "Baker, Bo"], ["Dunn, Di", "Zed, Unknown"])))
    r = client.post("/upload", files={"file": ("m.pdf", b"%PDF", "application/pdf")})
    assert "/ministering/import/" in str(r.url) and "Zed, Unknown" in r.text
    r = client.post(str(r.url), data={"match_0": ""})  # skip the unknown name
    assert re.search(r"/ministering/layouts/\d+$", str(r.url)) and "skipped" in r.text


def test_members_cannot_upload_ministering(client, monkeypatch):
    import app.main as main
    auth.create_user("member", PW)
    login(client, "member", PW)
    monkeypatch.setattr(main, "detect", lambda data: "ministering")
    r = client.post("/upload", files={"file": ("m.pdf", b"%PDF", "application/pdf")})
    assert "Only leaders" in r.text


def test_roles_on_users_page(client):
    login(client)
    uid = auth.create_user("someone", PW)
    client.post(f"/admin/users/{uid}/role", data={"role": "leader"})
    other = TestClient(client.app)
    login(other, "someone", PW)
    assert other.get("/ministering").status_code == 200
    assert other.get("/admin/users").status_code == 403
    client.post(f"/admin/users/{uid}/role", data={"role": "bogus"})  # falls back to member
    assert other.get("/ministering").status_code == 403

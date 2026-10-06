"""Ministering data layer and routes. Made-up names only."""
import re
from datetime import date

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
    with db.ward_connect(1) as c:
        m.sync_people(c, ROSTER)
        c.commit()  # don't hold a write lock while the app's own connections run
        yield c


# --- roster -----------------------------------------------------------------

def test_sync_people_tracks_moves_and_tags_move_ins(client):
    with db.ward_connect(1) as conn:
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

    layout_id = m.finish_import(conn, payload, {"Ford, Faye": ids(conn)["Ford, Fay"]}, "admin", "eq")
    p = ids(conn)
    assert m.structure(conn, layout_id) == [{"name": "North", "supervisor": "Okey, Austin", "groups": [
        {"ministers": [p["Able, Al"], p["Baker, Bo"]], "assigned": [p["Dunn, Di"], p["Ford, Fay"]]}]}]
    assert m.current_import(conn, "eq")["id"] == layout_id


def test_new_import_becomes_current_and_old_one_becomes_history(conn):
    p = ids(conn)
    old = m.finish_import(conn, m.match_names(conn, report_of((["Able, Al", "Baker, Bo"], ["Dunn, Di"]))), {}, "a", "eq")
    new = m.finish_import(conn, m.match_names(conn, report_of((["Able, Al", "Cole, Cy"], ["Eads, Em"]))), {}, "a", "eq")
    assert m.current_import(conn, "eq")["id"] == new
    hist = m.history(conn, "eq", exclude_layout=new)
    a, b = sorted((p["Able, Al"], p["Baker, Bo"]))
    assert f"{a}-{b}" in hist["companions"]
    assert f"{p['Able, Al']}-{p['Dunn, Di']}" in hist["ministered"]
    # The current import itself is not "history".
    assert not any(str(p["Cole, Cy"]) in k.split("-") for k in hist["companions"])
    assert old != new


# --- layouts ----------------------------------------------------------------

def test_create_layout_variants(conn):
    with pytest.raises(m.LayoutError):
        m.create_layout(conn, "eq", "x", "current", "a")  # nothing imported yet
    scratch = m.create_layout(conn, "eq", "", "scratch", "a")
    assert m.structure(conn, scratch) == [{"name": "District 1", "supervisor": "", "groups": []}]

    imported = m.finish_import(conn, m.match_names(conn, report_of((["Able, Al"], ["Dunn, Di"]))), {}, "a", "eq")
    current = m.create_layout(conn, "eq", "From current", "current", "a")
    assert m.structure(conn, current) == m.structure(conn, imported)
    scratch2 = m.create_layout(conn, "eq", "Scratch", "scratch", "a")
    assert m.structure(conn, scratch2) == [{"name": "North", "supervisor": "Okey, Austin", "groups": []}]
    copy = m.create_layout(conn, "eq", "Copy", "copy", "a", copy_from=current)
    assert m.structure(conn, copy) == m.structure(conn, current)


def test_save_structure_checks_version_readonly_and_people(conn):
    p = ids(conn)
    layout_id = m.create_layout(conn, "eq", "Draft", "scratch", "a")
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
    imported = m.finish_import(conn, m.match_names(conn, report_of((["Able, Al"], []))), {}, "a", "eq")
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


# --- checking an approved layout in LCR -------------------------------------

def _backdate_imports(conn):
    # Imports and approvals in a test land in the same second; push imports into the past.
    conn.execute("UPDATE layouts SET created_at = datetime('now', '-1 hour') WHERE kind = 'imported'")


def test_approved_layout_is_checked_by_the_next_import(conn):
    p = ids(conn)
    m.finish_import(conn, m.match_names(conn, report_of((["Able, Al", "Baker, Bo"], ["Dunn, Di"]))), {}, "a", "eq")
    _backdate_imports(conn)
    layout_id = m.create_layout(conn, "eq", "Fall", "current", "a")
    target = [{"name": "North", "supervisor": "", "groups": [
        {"ministers": [p["Able, Al"], p["Baker, Bo"]], "assigned": [p["Dunn, Di"], p["Eads, Em"]]}]}]
    m.save_structure(conn, layout_id, target, 1)
    assert m.lcr_check(conn, m.get_layout(conn, layout_id)) is None  # not approved yet
    m.update_meta(conn, layout_id, status="approved")
    assert m.lcr_check(conn, m.get_layout(conn, layout_id)) == {"state": "waiting"}
    assert [l["id"] for l in m.org_summary(conn, "eq")["to_check"]] == [layout_id]

    # LCR only got half of it.
    partial = m.finish_import(conn, m.match_names(conn, report_of((["Able, Al", "Baker, Bo"], []))), {}, "a", "eq")
    assert m.check_approved(conn, "eq", partial) == [{"id": layout_id, "name": "Fall", "differences": 1}]
    check = m.lcr_check(conn, m.get_layout(conn, layout_id))
    assert check["state"] == "mismatch" and check["differences"] == 1

    # Now it all made it in.
    full = m.finish_import(conn, m.match_names(conn, report_of(
        (["Able, Al", "Baker, Bo"], ["Dunn, Di", "Eads, Em"]))), {}, "a", "eq")
    assert m.check_approved(conn, "eq", full) == [{"id": layout_id, "name": "Fall", "differences": 0}]
    assert m.lcr_check(conn, m.get_layout(conn, layout_id))["state"] == "verified"
    assert m.org_summary(conn, "eq")["to_check"] == []
    assert m.check_approved(conn, "eq", full) == []  # verified layouts aren't checked again
    assert m.check_approved(conn, "rs", full) == []  # nor are the other organization's

    # Leaving Approved clears the check; approving again starts a new one.
    m.update_meta(conn, layout_id, status="draft")
    assert m.get_layout(conn, layout_id)["verified_at"] is None
    m.update_meta(conn, layout_id, status="approved")
    assert not m.get_layout(conn, layout_id)["verified_at"]
    m.update_meta(conn, layout_id, status="approved")  # unchanged status keeps the check as is
    m.mark_verified(conn, layout_id)
    assert m.lcr_check(conn, m.get_layout(conn, layout_id))["state"] == "verified"
    m.update_meta(conn, layout_id, status="approved")
    assert m.get_layout(conn, layout_id)["verified_at"]


def test_only_approved_layouts_can_be_marked_done(conn):
    layout_id = m.create_layout(conn, "eq", "Draft", "scratch", "a")
    with pytest.raises(m.LayoutError):
        m.mark_verified(conn, layout_id)


def test_lcr_check_flow_through_the_app(client, conn, monkeypatch):
    import app.main as main
    _leader(client)
    p = ids(conn)
    m.finish_import(conn, m.match_names(conn, report_of((["Able, Al", "Baker, Bo"], ["Dunn, Di"]))), {}, "a", "eq")
    _backdate_imports(conn)
    conn.commit()
    r = client.post("/ministering/eq/layouts", data={"name": "Fall", "start": "current"})
    layout_id = int(re.search(r"/ministering/layouts/(\d+)$", str(r.url)).group(1))
    state = client.get(f"/api/ministering/layouts/{layout_id}").json()
    state["districts"][0]["groups"][0]["assigned"].append(p["Eads, Em"])
    client.put(f"/api/ministering/layouts/{layout_id}/structure",
               json={"version": state["layout"]["version"], "districts": state["districts"]})
    client.patch(f"/api/ministering/layouts/{layout_id}", json={"status": "approved"})

    assert "Enter “Fall” in LCR, then re-import" in client.get("/").text
    assert "Upload and check" in client.get(f"/ministering/layouts/{layout_id}/changes").text
    assert "needs LCR check" in client.get("/ministering/eq").text

    monkeypatch.setattr(main, "detect", lambda data: "ministering")
    monkeypatch.setattr(main.ministering_report, "parse_pdf",
                        lambda data: report_of((["Able, Al", "Baker, Bo"], ["Dunn, Di"])))
    r = client.post("/upload", data={"org": "eq"}, files={"file": ("m.pdf", b"%PDF", "application/pdf")})
    assert str(r.url).endswith(f"/ministering/layouts/{layout_id}/changes")
    assert "doesn’t match LCR yet: 1 difference left" in r.text and "Assign <strong>Em Eads</strong>" in r.text
    assert "doesn’t match LCR yet (1 difference)" in client.get("/").text

    monkeypatch.setattr(main.ministering_report, "parse_pdf",
                        lambda data: report_of((["Able, Al", "Baker, Bo"], ["Dunn, Di", "Eads, Em"])))
    r = client.post("/upload", data={"org": "eq"}, files={"file": ("m.pdf", b"%PDF", "application/pdf")})
    assert "“Fall” is verified" in r.text and "Verified in LCR on" in r.text
    assert "match LCR yet" not in client.get("/").text and "Enter “Fall”" not in client.get("/").text
    assert "Verified in LCR" in client.get("/ministering/eq").text

    draft = m.create_layout(conn, "eq", "Other", "scratch", "a")
    conn.commit()
    assert "Only approved" in client.post(f"/ministering/layouts/{draft}/verify").text


# --- who ministers to whom --------------------------------------------------

def test_who_ministers_combines_orgs_and_marks_changes(conn):
    p = ids(conn)
    conn.execute("INSERT INTO people (name, gender, birth_year, birth_month, birth_day) VALUES ('Kid, Kip', 'M', ?, 1, 1)",
                 (date.today().year - 5,))
    eq = m.finish_import(conn, m.match_names(conn, report_of((["Able, Al", "Baker, Bo"], ["Dunn, Di", "Eads, Em"]))),
                         {}, "a", "eq")
    rs = m.finish_import(conn, m.match_names(conn, report_of((["Dunn, Di", "Ford, Fay"], ["Eads, Em"]))), {}, "a", "rs")
    rows, people = m.who_ministers(conn, {"eq": m.get_layout(conn, eq), "rs": m.get_layout(conn, rs)})
    by_name = {r["person"]["name"]: r for r in rows}
    assert by_name["Eads, Em"]["ministers"] == {"eq": [p["Able, Al"], p["Baker, Bo"]], "rs": [p["Dunn, Di"], p["Ford, Fay"]]}
    assert by_name["Cole, Cy"]["ministers"] == {"eq": [], "rs": []}  # adult with no ministers
    assert "Kid, Kip" not in by_name  # children aren't listed just for having no ministers
    assert [r["person"]["name"] for r in rows] == sorted(by_name, key=str.lower)
    assert not any(c for r in rows for c in r["changed"].values())  # current imports change nothing
    assert people[p["Able, Al"]]["display"] == "Al Able"

    rows, _ = m.who_ministers(conn, {"eq": m.get_layout(conn, eq)}, include_unassigned=False)
    assert [r["person"]["name"] for r in rows] == ["Dunn, Di", "Eads, Em"]

    draft = m.create_layout(conn, "eq", "Fall", "current", "a")
    m.save_structure(conn, draft, [{"name": "North", "groups": [
        {"ministers": [p["Able, Al"], p["Cole, Cy"]], "assigned": [p["Dunn, Di"]]}]}], 1)
    rows, _ = m.who_ministers(conn, {"eq": m.get_layout(conn, draft)}, include_unassigned=False)
    # Em loses her ministers in the draft: still listed so the change isn't hidden.
    assert [(r["person"]["name"], r["changed"]["eq"]) for r in rows] == [("Dunn, Di", True), ("Eads, Em", True)]


def test_who_ministers_page(client, conn):
    _leader(client)
    p = ids(conn)
    eq = m.finish_import(conn, m.match_names(conn, report_of((["Able, Al", "Baker, Bo"], ["Dunn, Di"]))), {}, "a", "eq")
    m.finish_import(conn, m.match_names(conn, report_of((["Dunn, Di", "Ford, Fay"], ["Eads, Em"]))), {}, "a", "rs")
    draft = m.create_layout(conn, "eq", "Fall plan", "current", "a")
    m.save_structure(conn, draft, [{"name": "North", "groups": [
        {"ministers": [p["Able, Al"], p["Cole, Cy"]], "assigned": [p["Dunn, Di"]]}]}], 1)
    m.set_tags(conn, p["Dunn, Di"], ["Needs extra care"])
    conn.commit()

    page = client.get("/ministering/assignments").text
    assert "Who ministers to whom" in page and "For Church Use Only" in page
    assert "Al Able &amp; Bo Baker" in page and "Di Dunn &amp; Fay Ford" in page
    assert "No ministers" in page and "Needs extra care" not in page  # tags are opt-in
    assert "chg-mark" not in page.split("<table")[1] and "marked New" not in page

    page = client.get(f"/ministering/assignments?eq={draft}&rs=none&tags=1&assigned_only=1").text
    assert "Fall plan (Draft)" in page and "Al Able &amp; Cy Cole" in page and "Relief Society:" not in page
    assert "1 marked New" in page and page.split("<table")[1].count("chg-mark") == 1
    assert "Needs extra care" in page and "No ministers</span>" not in page
    # A layout from the other organization, or one that doesn't exist, falls back to what's in LCR.
    for bad in (draft, 99999):
        assert "Current in LCR" in client.get(f"/ministering/assignments?rs={bad}").text.split("<h1>")[1]
    assert f"/ministering/assignments?eq={draft}" in client.get(f"/ministering/layouts/{draft}/changes").text
    assert "/ministering/assignments" in client.get("/ministering/people").text
    assert eq


# --- routes -----------------------------------------------------------------

def _leader(client):
    auth.create_user("leader", PW, role="leader")
    login(client, "leader", PW)


def test_members_cannot_see_ministering(client):
    auth.create_user("member", PW)
    login(client, "member", PW)
    assert "/ministering" not in client.get("/").text
    for path in ["/ministering", "/ministering/people", "/ministering/assignments", "/api/ministering/layouts/1"]:
        assert client.get(path).status_code == 403


def test_board_flow(client, conn):
    _leader(client)
    p = ids(conn)
    m.finish_import(conn, m.match_names(conn, report_of((["Able, Al", "Baker, Bo"], ["Dunn, Di"]))), {}, "a", "eq")
    conn.commit()

    r = client.post("/ministering/eq/layouts", data={"name": "Fall", "start": "current"})
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
    assert r.json() == {"name": "Fall", "status": "approved", "readonly": True, "verified": False}
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
    with db.ward_connect(1) as c:
        assert c.execute("SELECT COUNT(*) FROM birthdays").fetchone()[0] == len(ROSTER)

    monkeypatch.setattr(main, "detect", lambda data: "ministering")
    monkeypatch.setattr(main.ministering_report, "parse_pdf",
                        lambda data: report_of((["Able, Al", "Baker, Bo"], ["Dunn, Di", "Zed, Unknown"])))
    r = client.post("/upload", files={"file": ("m.pdf", b"%PDF", "application/pdf")})
    assert "/ministering/import/" in str(r.url) and "Zed, Unknown" in r.text
    assert '<option value="eq" selected>' in r.text  # inferred from the ministers
    r = client.post(str(r.url), data={"match_0": "", "org": "eq"})  # skip the unknown name
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


# --- Elders Quorum vs Relief Society ----------------------------------------

def test_org_is_inferred_from_ministers(conn):
    eq = m.match_names(conn, report_of((["Able, Al", "Baker, Bo"], ["Dunn, Di"])))
    rs = m.match_names(conn, report_of((["Dunn, Di", "Eads, Em"], ["Ford, Fay"])))
    mixed = m.match_names(conn, report_of((["Able, Al", "Dunn, Di"], [])))
    unknown = m.match_names(conn, report_of((["Nobody, Ned"], [])))
    assert [m.infer_org(conn, p) for p in (eq, rs, mixed, unknown)] == ["eq", "rs", None, None]


def test_orgs_keep_separate_imports_and_layouts(conn):
    eq = m.finish_import(conn, m.match_names(conn, report_of((["Able, Al"], ["Dunn, Di"]))), {}, "a", "eq")
    rs = m.finish_import(conn, m.match_names(conn, report_of((["Dunn, Di"], ["Ford, Fay"]))), {}, "a", "rs")
    assert m.current_import(conn, "eq")["id"] == eq and m.current_import(conn, "rs")["id"] == rs
    draft = m.create_layout(conn, "rs", "Sisters draft", "current", "a")
    assert m.structure(conn, draft) == m.structure(conn, rs)
    assert [l["id"] for l in m.list_layouts(conn, "eq")] == [eq]
    with pytest.raises(m.LayoutError):
        m.create_layout(conn, "eq", "x", "copy", "a", copy_from=draft)  # can't copy across orgs


def test_ministering_pages_per_org(client, conn):
    _leader(client)
    m.finish_import(conn, m.match_names(conn, report_of((["Dunn, Di", "Eads, Em"], ["Ford, Fay"]))), {}, "a", "rs")
    conn.commit()
    landing = client.get("/ministering").text
    assert "Elders Quorum" in landing and "Relief Society" in landing
    assert "Current Relief Society assignments imported" in client.get("/ministering/rs").text
    assert "No Elders Quorum assignments imported yet" in client.get("/ministering/eq").text
    assert client.get("/ministering/xx").status_code == 404
    r = client.post("/ministering/rs/layouts", data={"name": "RS fall", "start": "current"})
    state = client.get(f"/api/ministering/layouts/{r.url.path.rsplit('/', 1)[1]}").json()
    assert state["org"] == "rs" and state["minister_gender"] == "F" and state["assigned_scope"] == "F"


def test_upload_files_report_under_the_right_org(client, monkeypatch):
    import app.main as main
    _leader(client)
    with db.ward_connect(1) as c:
        m.sync_people(c, ROSTER)
    monkeypatch.setattr(main, "detect", lambda data: "ministering")

    # Sisters ministering, uploaded from the Elders Quorum page: the ministers win.
    monkeypatch.setattr(main.ministering_report, "parse_pdf",
                        lambda data: report_of((["Dunn, Di", "Eads, Em"], ["Ford, Fay"])))
    r = client.post("/upload", data={"org": "eq"}, files={"file": ("m.pdf", b"%PDF", "application/pdf")})
    assert "Imported the current Relief Society assignments" in r.text and "the ministers are sisters" in r.text

    # Mixed ministers: ask, then file it where the leader says.
    monkeypatch.setattr(main.ministering_report, "parse_pdf",
                        lambda data: report_of((["Able, Al", "Dunn, Di"], ["Ford, Fay"])))
    r = client.post("/upload", files={"file": ("m.pdf", b"%PDF", "application/pdf")})
    assert "/ministering/import/" in str(r.url) and "Whose assignments are these?" in r.text
    assert "Pick whether" in client.post(str(r.url), data={}).text
    r = client.post(str(r.url), data={"org": "eq"})
    assert "Imported the current Elders Quorum assignments" in r.text
    with db.ward_connect(1) as c:
        assert m.current_import(c, "eq") and m.current_import(c, "rs")


def test_migrates_layouts_to_elders_quorum(tmp_path, monkeypatch):
    import sqlite3
    from app import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "portal.db")
    old = sqlite3.connect(tmp_path / "portal.db")
    old.executescript("""
        CREATE TABLE layouts (id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft', is_current INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')));
        INSERT INTO layouts (name, kind, is_current, created_by) VALUES ('Old import', 'imported', 1, 'ty');
        INSERT INTO layouts (name, kind, status, created_by) VALUES ('Old plan', 'draft', 'approved', 'ty');
    """)
    old.commit()
    old.close()
    db.init()
    with db.ward_connect(1) as c:
        assert m.current_import(c, "eq")["name"] == "Old import"
        assert m.current_import(c, "rs") is None
        # Approved before the LCR check existed: counts as done, so the upgrade doesn't nag.
        assert m.org_summary(c, "eq")["to_check"] == []
        assert m.get_layout(c, 2)["verified_at"]

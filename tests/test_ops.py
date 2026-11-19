"""Backups, restore, and the status page. Made-up names only."""
import re
import sqlite3
import zipfile
from datetime import datetime

import pytest

from app import auth, backup, cli, config, db, groupme, health, ministering as m
from tests.conftest import login
from tests.test_ministering import PW, ROSTER


@pytest.fixture
def ward(client):
    with db.ward_connect(1) as c:
        m.sync_people(c, ROSTER)
    return client


def names_in(path) -> set[str]:
    with zipfile.ZipFile(path) as z:
        return set(z.namelist())


def count_people(path) -> int:
    return sqlite3.connect(path).execute("SELECT COUNT(*) FROM people").fetchone()[0]


# --- backups ----------------------------------------------------------------

def test_a_backup_holds_every_database_but_not_the_session_key(ward):
    path = backup.create("manual")
    assert names_in(path) == {"portal.db", "wards/ward-1.db"}
    assert "secret_key" not in " ".join(names_in(path))
    assert [b["name"] for b in backup.listing()] == [path.name]
    assert backup.path_of(path.name) == path


def test_backups_are_pruned_per_kind(ward, monkeypatch):
    monkeypatch.setitem(backup.KEEP, "manual", 2)
    made = [backup.create("manual") for _ in range(4)]     # same second: names still stay unique
    assert len({p.name for p in made}) == 4
    kept = [b["name"] for b in backup.listing("manual")]
    assert kept == [made[3].name, made[2].name]            # the two newest survive, newest first
    backup.create("auto")
    assert len(backup.listing("manual")) == 2 and len(backup.listing("auto")) == 1


def test_only_names_we_made_can_be_fetched(ward):
    backup.create("manual")
    for bad in ("../portal.db", "..%2Fportal.db", "manual-1.zip", "notes.txt", "manual-20260101-000000.zip"):
        assert backup.path_of(bad) is None


def test_nightly_backup_runs_once_after_3am(ward, monkeypatch):
    # backup.create() names files from the clock, so freeze it to the day under test.
    clock = {"now": datetime(2026, 11, 18, 3, 5)}
    monkeypatch.setattr(backup.groupme, "now", lambda: clock["now"])
    assert backup.run_due(datetime(2026, 11, 18, 2, 30)) is None
    first = backup.run_due(clock["now"])
    assert first is not None and first.name.startswith("auto-20261118")
    clock["now"] = datetime(2026, 11, 18, 9, 0)
    assert backup.run_due(clock["now"]) is None                      # one a day
    clock["now"] = datetime(2026, 11, 19, 3, 5)
    assert backup.run_due(clock["now"]) is not None                  # and again the next day


def test_a_failing_backup_is_reported(ward, monkeypatch):
    def boom(kind):
        raise OSError("disk full")
    monkeypatch.setattr(backup, "create", boom)
    assert backup.run_due(datetime(2026, 11, 18, 4, 0)) is None
    assert "disk full" in backup.last_error()
    monkeypatch.undo()


def test_restore_puts_the_data_back_and_keeps_a_copy(ward):
    snapshot = backup.create("manual")
    with db.ward_connect(1) as c:
        c.execute("DELETE FROM people")
    with db.connect() as c:
        c.execute("INSERT INTO wards (name, feed_token) VALUES ('Later Ward', 'tok')")
    db.ward_path(2).write_bytes(b"")     # a ward file that isn't in the backup
    assert count_people(db.ward_path(1)) == 0

    files = backup.restore(snapshot)
    assert files == ["portal.db", "wards/ward-1.db"]
    assert count_people(db.ward_path(1)) == len(ROSTER)
    assert not db.ward_path(2).exists()                      # no stray ward data left to be picked up
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM wards WHERE name = 'Later Ward'").fetchone()[0] == 0
    safety = backup.listing("pre-restore")
    assert len(safety) == 1 and count_people_in_zip(backup.path_of(safety[0]["name"])) == 0


def count_people_in_zip(path, tmp=None) -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as d, zipfile.ZipFile(path) as z:
        z.extract("wards/ward-1.db", d)
        return count_people(f"{d}/wards/ward-1.db")


def test_restore_refuses_anything_odd(ward, tmp_path):
    not_zip = tmp_path / "x.zip"
    not_zip.write_text("hello")
    with pytest.raises(backup.BackupError):
        backup.restore(not_zip)
    with pytest.raises(backup.BackupError):
        backup.restore(tmp_path / "missing.zip")

    def make(name, files):
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as z:
            for arc, data in files.items():
                z.writestr(arc, data)
        return path
    with pytest.raises(backup.BackupError, match="Unexpected"):
        backup.restore(make("evil.zip", {"portal.db": b"", "../../etc/passwd": b"x"}))
    with pytest.raises(backup.BackupError, match="no portal.db"):
        backup.restore(make("empty.zip", {"wards/ward-1.db": b""}))
    with pytest.raises(backup.BackupError, match="damaged"):
        backup.restore(make("junk.zip", {"portal.db": b"this is not a database" * 100}))
    assert count_people(db.ward_path(1)) == len(ROSTER)      # nothing was touched


def test_cli_restore_asks_first(ward, monkeypatch, capsys):
    snapshot = backup.create("manual")
    monkeypatch.setattr("sys.argv", ["cli", "restore-backup", str(snapshot)])
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    assert cli.main() == 1 and "Cancelled" in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["cli", "restore-backup", str(snapshot), "--yes"])
    assert cli.main() == 0


# --- status page ------------------------------------------------------------

def test_status_checks_flag_what_is_stale_or_broken(ward):
    with db.ward_connect(1) as c:
        checks = {x["label"]: x for x in health.ward_checks(c)}
        assert checks["Member List"]["state"] == "bad"
        c.execute("INSERT INTO uploads (report, uploaded_by, summary, uploaded_at) VALUES "
                  "('directory', 'ty', '{}', datetime('now', '-30 days'))")
        assert {x["label"]: x for x in health.ward_checks(c)}["Member List"]["state"] == "warn"
        c.execute("INSERT INTO uploads (report, uploaded_by, summary) VALUES ('directory', 'ty', '{}')")
        assert {x["label"]: x for x in health.ward_checks(c)}["Member List"]["state"] == "ok"

        groupme.set_bot_id(c, "birthdays", "abcdef0123456789abcdef0123")
        assert {x["label"]: x for x in health.ward_checks(c)}["Birthday shout-outs"]["state"] == "warn"  # posting is off in tests
        assert health.problems(c, False) == []
        c.execute("INSERT INTO ward_settings (key, value) VALUES ('groupme:birthdays:last', ?)",
                  ('{"date": "2026-11-18", "at": "Wed, Nov 18 at 8:05 AM", "ok": false, "error": "HTTP 404", "fails": 3}',))
        config.GROUPME_ENABLED = True
        try:
            bad = {x["label"]: x for x in health.ward_checks(c)}["Birthday shout-outs"]
            assert bad["state"] == "bad" and "HTTP 404" in bad["text"]
            assert health.problems(c, False)[0]["text"].startswith("Birthday shout-outs")
        finally:
            config.GROUPME_ENABLED = False


def test_server_checks(ward):
    state = {c["label"]: c for c in health.server_checks()}
    assert state["Nightly backup"]["state"] == "warn"            # switched off in tests
    assert "free" in state["Disk space"]["text"]


def test_status_page_access_and_content(client):
    login(client)
    page = client.get("/admin/status")
    assert page.status_code == 200 and "Backups" in page.text and "Disk space" in page.text
    assert client.post("/admin/backups").status_code == 200
    name = re.search(r'/admin/backups/(manual-[\w-]+\.zip)', client.get("/admin/status").text).group(1)
    dl = client.get(f"/admin/backups/{name}")
    assert dl.status_code == 200 and dl.headers["content-type"] == "application/zip"
    assert zipfile.ZipFile(__import__("io").BytesIO(dl.content)).testzip() is None
    assert client.get("/admin/backups/..%2Fportal.db").status_code == 404
    assert client.get("/admin/backups/nope.zip").status_code == 404
    assert "PUBLIC_URL" in page.text


def test_public_link_check(client, monkeypatch):
    login(client)
    assert "Set PUBLIC_URL" in client.post("/admin/status/check-public", follow_redirects=True).text
    monkeypatch.setattr(config, "PUBLIC_URL", "https://example.test")
    monkeypatch.setattr(health, "check_public_link", lambda url: (False, f"Couldn’t reach {url}"))
    assert "Couldn’t reach https://example.test" in client.post("/admin/status/check-public", follow_redirects=True).text


def test_ward_admins_see_status_but_not_backups(client):
    auth.create_user("wardadmin", PW, role="ward_admin")
    login(client, "wardadmin", PW)
    page = client.get("/admin/status")
    assert page.status_code == 200 and "Member List" in page.text and "Back up now" not in page.text
    assert client.post("/admin/backups").status_code == 403
    assert client.get("/admin/backups/manual-20260101-000000.zip").status_code == 403


def test_leaders_and_members_cannot_see_status(client):
    for role in ("leader", "member"):
        auth.create_user(role, PW, role=role)
        login(client, role, PW)
        assert client.get("/admin/status").status_code == 403


def test_dashboard_warns_admins_about_failing_bots(client):
    login(client)
    with db.ward_connect(1) as c:
        groupme.set_bot_id(c, "birthdays", "abcdef0123456789abcdef0123")
        c.execute("INSERT INTO ward_settings (key, value) VALUES ('groupme:birthdays:last', ?)",
                  ('{"date": "2026-11-18", "at": "Wed", "ok": false, "error": "HTTP 404", "fails": 3}',))
    config.GROUPME_ENABLED = True
    try:
        assert "the last post failed" in client.get("/").text
    finally:
        config.GROUPME_ENABLED = False

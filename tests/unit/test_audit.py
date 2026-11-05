import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.build_repo_fixture import REPO
from tests.unit.fake_idp import FakeIdP
from tests.unit.test_auth import AUTH, sign_in

from munki_web_studio.app import create_app
from munki_web_studio.audit import AuditLog
from munki_web_studio.repository.config import Repository, Settings


def test_entries_are_append_only_and_chained(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.sqlite")
    for i in range(3):
        log.record(actor="u", actor_name="User", interface="web", action=f"step {i}")
    assert [e.action for e in log.entries()] == ["step 2", "step 1", "step 0"]
    assert log.verify() is None
    db = sqlite3.connect(tmp_path / "audit.sqlite")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE audit SET actor = 'someone else' WHERE id = 2")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM audit")
    db.execute("DROP TRIGGER audit_no_update")
    db.execute("UPDATE audit SET actor = 'someone else' WHERE id = 2")
    db.commit()
    assert log.verify() == 2


def test_entries_survive_a_restart(tmp_path: Path) -> None:
    AuditLog(tmp_path / "audit.sqlite").record(
        actor="u", actor_name="U", interface="cli", action="x"
    )
    assert AuditLog(tmp_path / "audit.sqlite").entries()[0].interface == "cli"


def test_changes_and_sign_ins_are_recorded(tmp_path: Path) -> None:
    idp = FakeIdP()
    settings = Settings(
        repositories=(Repository(id="fixture", name="Fixture", root=REPO.resolve()),),
        state_dir=tmp_path / "state",
        rescan_seconds=0,
        auth=AUTH,
    )
    app = create_app(settings, static_dir=tmp_path / "none", oidc_http=idp.http())
    with TestClient(app, base_url="http://localhost", follow_redirects=False) as client:
        assert client.post("/api/repositories/fixture/rescan").status_code == 401
        sign_in(client, idp, roles=["editors"])
        token = client.get("/api/session").json()["csrf_token"]
        client.post(
            "/api/repositories/fixture/rescan",
            headers={"X-CSRF-Token": token, "X-Studio-Client": "cli"},
        )
        assert client.get("/api/audit").status_code == 403
        client.cookies.clear()
        sign_in(client, idp, roles=["admins"])
        entries = client.get("/api/audit").json()
    actions = [(e["action"], e["outcome"], e["interface"]) for e in reversed(entries)]
    assert actions[0] == ("POST /api/repositories/fixture/rescan", "refused 401", "web")
    assert ("sign-in", "ok", "web") in actions
    assert ("POST /api/repositories/fixture/rescan", "ok", "cli") in actions
    rescan = next(e for e in entries if e["outcome"] == "ok" and e["action"].startswith("POST"))
    assert rescan["actor_name"] == "Example Admin" and rescan["repo_id"] == "fixture"

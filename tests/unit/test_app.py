from pathlib import Path

from fastapi.testclient import TestClient

from munki_web_studio import STUDIO, __version__
from munki_web_studio.app import create_app


def test_health_reports_version() -> None:
    with TestClient(create_app(), base_url="http://localhost") as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "name": STUDIO.name, "version": __version__}


def test_serves_built_workspace(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>workspace</title>")
    with TestClient(create_app(static_dir=tmp_path), base_url="http://localhost") as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "<title>workspace</title>" in response.text
        assert client.get("/api/health").status_code == 200


def test_explains_missing_workspace(tmp_path: Path) -> None:
    with TestClient(create_app(static_dir=tmp_path), base_url="http://localhost") as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "pnpm build" in response.text


def test_refuses_unknown_host_names(tmp_path: Path) -> None:
    with TestClient(create_app(static_dir=tmp_path), base_url="http://localhost") as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/health", headers={"host": "127.0.0.1:3186"}).status_code == 200
        rebound = client.get("/api/health", headers={"host": "attacker.example"})
        assert rebound.status_code == 400
        assert (
            client.get("/api/repositories", headers={"host": "attacker.example"}).status_code == 400
        )

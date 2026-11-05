from pathlib import Path

from fastapi.testclient import TestClient

from munki_web_studio import STUDIO, __version__
from munki_web_studio.app import create_app


def test_health_reports_version() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "name": STUDIO.name, "version": __version__}


def test_serves_built_workspace(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>workspace</title>")
    with TestClient(create_app(static_dir=tmp_path)) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "<title>workspace</title>" in response.text
        assert client.get("/api/health").status_code == 200


def test_explains_missing_workspace(tmp_path: Path) -> None:
    with TestClient(create_app(static_dir=tmp_path)) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "pnpm build" in response.text

from fastapi.testclient import TestClient

from munki_web_studio import STUDIO, __version__
from munki_web_studio.app import create_app


def test_health_reports_version() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "name": STUDIO.name, "version": __version__}

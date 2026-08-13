from fastapi.testclient import TestClient

from v4vapp_dash import __version__
from v4vapp_dash.main import create_app


def test_health_is_open() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["network"] == "regtest"


def test_root_is_open() -> None:
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "v4vapp-dash"

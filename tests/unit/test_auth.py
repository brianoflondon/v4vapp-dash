from fastapi.testclient import TestClient

from v4vapp_dash.main import create_app


def test_metrics_requires_key() -> None:
    client = TestClient(create_app())
    response = client.get("/metrics")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "unauthorized"
    assert response.headers.get("www-authenticate") == "ApiKey"


def test_metrics_accepts_current_key() -> None:
    client = TestClient(create_app())
    response = client.get("/metrics", headers={"X-API-Key": "test-api-key"})
    assert response.status_code == 200


def test_metrics_accepts_previous_key() -> None:
    client = TestClient(create_app())
    response = client.get("/metrics", headers={"X-API-Key": "test-api-key-prev"})
    assert response.status_code == 200


def test_metrics_rejects_wrong_key() -> None:
    client = TestClient(create_app())
    response = client.get("/metrics", headers={"X-API-Key": "nope"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"

import httpx
import pytest
from fastapi.testclient import TestClient

from v4vapp_dash import __version__
from v4vapp_dash.config import get_settings
from v4vapp_dash.main import create_app


def test_health_is_open() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["network"] == "regtest"
    assert body["mongo"] is None


def test_root_is_open() -> None:
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "v4vapp-dash"


def test_startup_survives_dashd_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASH_RPC_URL", "http://127.0.0.1:19998")
    monkeypatch.setenv("DASH_RPC_PASSWORD", "not-the-placeholder")
    get_settings.cache_clear()

    async def boom(self) -> dict:
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr("v4vapp_dash.main.Dashd.getblockchaininfo", boom)
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert "dashd" not in response.json()
    assert response.json()["status"] == "degraded"


def test_dashd_reconnects_after_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    monkeypatch.setenv("DASH_RPC_URL", "http://127.0.0.1:19998")
    monkeypatch.setenv("DASH_RPC_PASSWORD", "not-the-placeholder")
    get_settings.cache_clear()
    monkeypatch.setattr("v4vapp_dash.main._RPC_RETRY_S", 0.05)
    calls = {"n": 0}

    async def flaky(self) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("timed out")
        return {
            "chain": "test",
            "blocks": 1,
            "headers": 1,
            "verificationprogress": 1.0,
            "initialblockdownload": False,
            "pruned": True,
        }

    monkeypatch.setattr("v4vapp_dash.main.Dashd.getblockchaininfo", flaky)
    with TestClient(create_app()) as client:
        first = client.get("/health")
        assert first.status_code == 200
        assert "dashd" not in first.json()
        deadline = time.monotonic() + 2.0
        body = first.json()
        while time.monotonic() < deadline and "dashd" not in body:
            time.sleep(0.05)
            body = client.get("/health").json()
        assert body.get("dashd", {}).get("chain") == "test"
        assert body["status"] == "ok"

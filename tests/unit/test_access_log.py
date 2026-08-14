import logging

import pytest
from fastapi.testclient import TestClient

from v4vapp_dash.main import create_app


def _request_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "v4vapp_dash" and r.getMessage() == "request"]


def test_health_request_is_not_info(caplog: pytest.LogCaptureFixture) -> None:
    client = TestClient(create_app())
    caplog.set_level(logging.DEBUG, logger="v4vapp_dash")
    caplog.clear()
    response = client.get("/health")
    assert response.status_code == 200
    info = [r for r in _request_records(caplog) if r.levelno >= logging.INFO]
    assert info == []
    debug = [r for r in _request_records(caplog) if r.levelno == logging.DEBUG]
    assert any(getattr(r, "path", None) == "/health" for r in debug)


def test_metrics_emits_info_request(caplog: pytest.LogCaptureFixture) -> None:
    client = TestClient(create_app())
    caplog.set_level(logging.INFO, logger="v4vapp_dash")
    caplog.clear()
    response = client.get("/metrics", headers={"X-API-Key": "test-api-key"})
    assert response.status_code == 200
    reqs = _request_records(caplog)
    assert any(
        r.levelno == logging.INFO
        and getattr(r, "path", None) == "/metrics"
        and getattr(r, "status", None) == 200
        for r in reqs
    )


def test_unhandled_exception_emits_request_500(caplog: pytest.LogCaptureFixture) -> None:
    app = create_app()

    @app.get("/__boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    client = TestClient(app, raise_server_exceptions=False)
    caplog.set_level(logging.INFO, logger="v4vapp_dash")
    caplog.clear()
    response = client.get("/__boom")
    assert response.status_code == 500
    reqs = _request_records(caplog)
    assert any(
        r.levelno >= logging.ERROR
        and getattr(r, "status", None) == 500
        and getattr(r, "path", None) == "/__boom"
        for r in reqs
    )

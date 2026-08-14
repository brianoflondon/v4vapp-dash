import pytest

from v4vapp_dash.config import get_settings
from v4vapp_dash.limits.hive_config import reset_rate_window_cache
from v4vapp_dash.logging.setup import reset_logging
from v4vapp_dash.quotes.service import reset_quote_cache


@pytest.fixture(autouse=True)
def _test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASH_API_KEY", "test-api-key")
    monkeypatch.setenv("DASH_API_KEY_PREV", "test-api-key-prev")
    monkeypatch.setenv("DASH_NETWORK", "regtest")
    monkeypatch.setenv("DASH_DOCS_ENABLED", "true")
    monkeypatch.setenv("DASH_RPC_PASSWORD", "")
    monkeypatch.setenv("COINMARKETCAP_API_KEY", "")
    monkeypatch.setenv("MONGO_URI", "")
    monkeypatch.setenv("DASH_XPUB", "")
    monkeypatch.setenv("DASH_MASTER_FINGERPRINT", "")
    get_settings.cache_clear()
    reset_quote_cache()
    reset_rate_window_cache()
    reset_logging()
    yield
    reset_logging()
    get_settings.cache_clear()
    reset_quote_cache()
    reset_rate_window_cache()

import logging

import pytest

from v4vapp_dash.config import get_settings
from v4vapp_dash.db.wallet_state import _brief
from v4vapp_dash.logging.redact import REDACTED, SecretRedactFilter

XPUB = (
    "tpubDC5FSnBiZDMmhiuCmWAYsLwgLYrrT9rAqvTySfuCCrgsWz8wxMXUS9Tb9iVMvcRbv"
    "FcAHGkMD5Kx8koh4GquNGNTfohfk7pgjhaPCdXpoba"
)
WIF = "K" + ("1" * 51)
MONGO_URI = "mongodb://alice:s3cret@mongo:27017/v4vapp"


def _record(msg: str, args: tuple = (), **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="v4vapp_dash",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_redacts_interpolated_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASH_RPC_PASSWORD", "s3cret-rpc")
    get_settings.cache_clear()
    record = _record("rpc %s", ("s3cret-rpc",))
    assert SecretRedactFilter().filter(record) is True
    assert record.getMessage() == f"rpc {REDACTED}"
    assert "s3cret-rpc" not in str(record.args)


def test_redacts_mongo_uri_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGO_URI", MONGO_URI)
    get_settings.cache_clear()
    record = _record(f"connecting {MONGO_URI} ok")
    SecretRedactFilter().filter(record)
    assert MONGO_URI not in record.getMessage()
    assert "alice:s3cret" not in record.getMessage()
    assert "connecting" in record.getMessage()
    assert "ok" in record.getMessage()


def test_redacts_mongo_uri_shape_without_settings() -> None:
    record = _record(f"uri={MONGO_URI} done")
    SecretRedactFilter().filter(record)
    assert "alice:s3cret" not in record.getMessage()
    assert record.getMessage().startswith("uri=")
    assert record.getMessage().endswith(" done")


def test_redacts_wif_shaped_string() -> None:
    record = _record(f"key material {WIF} loaded")
    SecretRedactFilter().filter(record)
    assert WIF not in record.getMessage()
    assert "key material" in record.getMessage()
    assert "loaded" in record.getMessage()


def test_token_only_xpub_keeps_wallet_state_sentence() -> None:
    brief = _brief(XPUB)
    msg = (
        f"dash_wallet_state[regtest] fingerprint/xpub does not match configured material "
        f"(stored xpub={brief}; env xpub={brief}). First boot locks the xpub for this network"
    )
    record = _record(msg)
    SecretRedactFilter().filter(record)
    text = record.getMessage()
    assert brief not in text
    assert XPUB not in text
    assert "dash_wallet_state[regtest]" in text
    assert "First boot locks the xpub for this network" in text
    assert REDACTED in text


def test_password_equal_to_network_does_not_redact_regtest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_password = "long-unique-api-key-value"
    monkeypatch.setenv("DASH_RPC_PASSWORD", "regtest")
    monkeypatch.setenv("DASH_NETWORK", "regtest")
    monkeypatch.setenv("DASH_API_KEY", long_password)
    get_settings.cache_clear()
    record = _record("rpc %s", (long_password,), network="regtest")
    SecretRedactFilter().filter(record)
    assert record.network == "regtest"
    assert record.getMessage() == f"rpc {REDACTED}"
    assert long_password not in record.getMessage()


def test_extra_secret_keys_redacted_invoice_id_untouched() -> None:
    record = _record(
        "ok",
        dash_rpc_password="hunter2",
        xpub=XPUB,
        invoice_id="inv-99",
    )
    SecretRedactFilter().filter(record)
    assert record.dash_rpc_password == REDACTED
    assert record.xpub == REDACTED
    assert record.invoice_id == "inv-99"


def test_reads_settings_inside_filter_not_init(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASH_RPC_PASSWORD", "first-secret")
    get_settings.cache_clear()
    filt = SecretRedactFilter()
    monkeypatch.setenv("DASH_RPC_PASSWORD", "second-secret")
    get_settings.cache_clear()
    record = _record("pw=%s", ("second-secret",))
    filt.filter(record)
    assert "second-secret" not in record.getMessage()
    assert "first-secret" not in record.getMessage()
    assert record.getMessage() == f"pw={REDACTED}"


def test_filter_survives_settings_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> None:
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr("v4vapp_dash.config.get_settings", _boom)
    record = _record(f"xpub is {XPUB}")
    assert SecretRedactFilter().filter(record) is True
    assert XPUB not in record.getMessage()

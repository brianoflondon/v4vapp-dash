from __future__ import annotations

import logging
import re
from typing import Any

from v4vapp_dash.logging.mylogger import LOG_RECORD_BUILTIN_ATTRS

REDACTED = "***"

_SECRET_SETTING_FIELDS = (
    "dash_api_key",
    "dash_api_key_prev",
    "dash_rpc_password",
    "mongo_uri",
    "dash_xpub",
    "coinmarketcap_api_key",
)

_EXTRA_KEY_RE = re.compile(
    r"(?i)(password|secret|token|api_key|authorization|mnemonic|xprv|xpub|seed|wif|mongo_uri|rpc_password)"
)

# Full extended-key payloads (xpub/tpub/…) and WIF-shaped strings.
_XPUB_RE = re.compile(r"(?:xprv|xpub|tpub|tprv|ypub|yprv|zpub|zprv)[1-9A-HJ-NP-Za-km-z]{20,}")
_WIF_RE = re.compile(r"[5KL][1-9A-HJ-NP-Za-km-z]{50,}")
_MONGO_URI_RE = re.compile(r"mongodb(?:\+srv)?://[^\s/:]+:[^\s/@]+@[^\s]+")


def _known_secret_values(settings: Any) -> tuple[str, ...]:
    if settings is None:
        return ()
    values: list[str] = []
    for field in _SECRET_SETTING_FIELDS:
        val = getattr(settings, field, None)
        if isinstance(val, str) and val:
            values.append(val)
    return tuple(values)


def _redact_text(text: str, secrets: tuple[str, ...]) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            text = text.replace(secret, REDACTED)
    text = _XPUB_RE.sub(REDACTED, text)
    text = _WIF_RE.sub(REDACTED, text)
    text = _MONGO_URI_RE.sub(REDACTED, text)
    return text


def _redact_value(val: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(val, str):
        return _redact_text(val, secrets)
    if isinstance(val, tuple):
        return tuple(_redact_value(v, secrets) for v in val)
    if isinstance(val, list):
        return [_redact_value(v, secrets) for v in val]
    if isinstance(val, dict):
        return {k: _redact_value(v, secrets) for k, v in val.items()}
    return val


def _redact_extra(key: str, val: Any, secrets: tuple[str, ...]) -> Any:
    if _EXTRA_KEY_RE.search(str(key)):
        if isinstance(val, str):
            return REDACTED if val else val
        if val is None:
            return val
        return REDACTED
    return _redact_value(val, secrets)


def _redact_record(record: logging.LogRecord, secrets: tuple[str, ...]) -> None:
    if isinstance(record.msg, str):
        record.msg = _redact_text(record.msg, secrets)
    if record.args:
        record.args = _redact_value(record.args, secrets)
    for key, val in list(record.__dict__.items()):
        if key in LOG_RECORD_BUILTIN_ATTRS:
            continue
        record.__dict__[key] = _redact_extra(key, val, secrets)


class SecretRedactFilter(logging.Filter):
    """Defense-in-depth redaction. Reads current Settings inside filter(), not __init__."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from v4vapp_dash.config import get_settings

            settings = get_settings()
        except Exception:
            settings = None
        secrets = _known_secret_values(settings)
        _redact_record(record, secrets)
        return True

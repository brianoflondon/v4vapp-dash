from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from typing import Any

import httpx

from v4vapp_dash.api.errors import ApiError
from v4vapp_dash.config import get_settings
from v4vapp_dash.models.quote import Quote

SATS_PER_BTC = 100_000_000
DUFFS_PER_DASH = Decimal("100000000")
CMC_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"

_cache: Quote | None = None
_cache_until: float = 0.0


def duffs_from_sats(sats: int, dash_btc: Decimal) -> int:
    """Always ceil so the customer cannot underpay by rounding."""
    if sats < 1:
        raise ValueError("sats must be >= 1")
    if dash_btc <= 0:
        raise ValueError("dash_btc must be > 0")
    sats_per_dash = Decimal(SATS_PER_BTC) * dash_btc
    raw = (Decimal(sats) * DUFFS_PER_DASH) / sats_per_dash
    return int(raw.to_integral_value(rounding=ROUND_CEILING))


def dash_amount_string(duffs: int) -> str:
    return f"{(Decimal(duffs) / DUFFS_PER_DASH):.8f}"


def rpc_dash_to_duffs(amount: Any) -> int:
    """dashd returns DASH as JSON floats — never use float * 1e8."""
    return int((Decimal(str(amount)) * DUFFS_PER_DASH).to_integral_value())


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_quote(source: str, btc_usd: Decimal, dash_usd: Decimal, dash_btc: Decimal) -> Quote:
    duffs = duffs_from_sats(1, dash_btc)  # placeholder; caller overwrites per invoice
    return Quote(
        source=source,
        fetched_at=_now_iso(),
        btc_usd=btc_usd,
        dash_usd=dash_usd,
        dash_btc=dash_btc,
        sats_per_dash=Decimal(SATS_PER_BTC) * dash_btc,
        ttl_s=60,
        duffs_quoted=duffs,
        dash_quoted=dash_amount_string(duffs),
    )


def quote_for_sats(sats: int, quote: Quote) -> Quote:
    duffs = duffs_from_sats(sats, quote.dash_btc)
    return quote.model_copy(
        update={"duffs_quoted": duffs, "dash_quoted": dash_amount_string(duffs)}
    )


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _from_coingecko(payload: dict[str, Any]) -> Quote:
    dash = payload["dash"]
    btc = payload["bitcoin"]
    dash_btc = _dec(dash["btc"])
    dash_usd = _dec(dash["usd"])
    btc_usd = _dec(btc["usd"])
    return _build_quote("coingecko", btc_usd, dash_usd, dash_btc)


def _from_cmc(payload: dict[str, Any]) -> Quote:
    data = payload["data"]
    btc_usd = _dec(data["BTC"]["quote"]["USD"]["price"])
    dash_usd = _dec(data["DASH"]["quote"]["USD"]["price"])
    dash_btc = dash_usd / btc_usd
    return _build_quote("coinmarketcap", btc_usd, dash_usd, dash_btc)


def fetch_quote(*, client: httpx.Client | None = None, force: bool = False) -> Quote:
    global _cache, _cache_until
    now = time.monotonic()
    if not force and _cache is not None and now < _cache_until:
        return _cache

    settings = get_settings()
    own_client = client is None
    http = client or httpx.Client(timeout=8.0)
    last_error: Exception | None = None
    try:
        try:
            resp = http.get(
                settings.coingecko_url,
                params={"ids": "dash,bitcoin", "vs_currencies": "btc,usd"},
            )
            resp.raise_for_status()
            quote = _from_coingecko(resp.json())
            _cache, _cache_until = quote, now + quote.ttl_s
            return quote
        except Exception as exc:  # noqa: BLE001 — fallback is the point
            last_error = exc

        if settings.coinmarketcap_api_key:
            try:
                resp = http.get(
                    CMC_URL,
                    params={"symbol": "DASH,BTC", "convert": "USD"},
                    headers={"X-CMC_PRO_API_KEY": settings.coinmarketcap_api_key},
                )
                resp.raise_for_status()
                quote = _from_cmc(resp.json())
                _cache, _cache_until = quote, now + quote.ttl_s
                return quote
            except Exception as exc:  # noqa: BLE001
                last_error = exc
    finally:
        if own_client:
            http.close()

    raise ApiError(
        422,
        "quote_unavailable",
        f"All price sources failed: {last_error}",
    )


def reset_quote_cache() -> None:
    global _cache, _cache_until
    _cache = None
    _cache_until = 0.0

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

import httpx

from v4vapp_dash.config import get_settings


@dataclass(frozen=True)
class RateWindow:
    hours: int
    sats: int


@dataclass(frozen=True)
class InvoiceFees:
    conv_fee_sats: int
    routing_fee_sats: int
    total_fee_sats: int
    sats_collect: int
    conv_fee_percent: str
    conv_fee_base_sats: int


@dataclass(frozen=True)
class HiveInvoiceConfig:
    windows: tuple[RateWindow, ...]
    conv_fee_percent: Decimal
    conv_fee_sats: int
    routing_fee_sats: int
    minimum_invoice_payment_sats: int
    maximum_invoice_payment_sats: int


DEFAULT_WINDOWS: tuple[RateWindow, ...] = (
    RateWindow(hours=4, sats=600_000),
    RateWindow(hours=72, sats=1_200_000),
    RateWindow(hours=168, sats=2_000_000),
)

DEFAULT_CONFIG = HiveInvoiceConfig(
    windows=DEFAULT_WINDOWS,
    conv_fee_percent=Decimal("0.029"),
    conv_fee_sats=50,
    routing_fee_sats=300,
    minimum_invoice_payment_sats=1,
    maximum_invoice_payment_sats=180_000,
)

_cache: HiveInvoiceConfig | None = None
_cache_until: float = 0.0


def reset_rate_window_cache() -> None:
    global _cache, _cache_until
    _cache = None
    _cache_until = 0.0


def fetch_rate_windows(
    *, client: httpx.Client | None = None, force: bool = False
) -> list[RateWindow]:
    return list(fetch_hive_config(client=client, force=force).windows)


def fetch_hive_config(
    *, client: httpx.Client | None = None, force: bool = False
) -> HiveInvoiceConfig:
    """Live Hive invoice limits/fees from GET /v1. Falls back to last cache, then defaults."""
    global _cache, _cache_until
    now = time.monotonic()
    if not force and _cache is not None and now < _cache_until:
        return _cache

    settings = get_settings()
    own = client is None
    http = client or httpx.Client(timeout=8.0)
    try:
        resp = http.get(settings.v4v_status_url)
        resp.raise_for_status()
        parsed = _parse_config(resp.json().get("config") or {}, settings.dash_routing_fee_sats)
        _cache, _cache_until = parsed, now + 60
        return parsed
    except Exception:
        if _cache is not None:
            return _cache
        return HiveInvoiceConfig(
            windows=DEFAULT_WINDOWS,
            conv_fee_percent=DEFAULT_CONFIG.conv_fee_percent,
            conv_fee_sats=DEFAULT_CONFIG.conv_fee_sats,
            routing_fee_sats=settings.dash_routing_fee_sats,
            minimum_invoice_payment_sats=DEFAULT_CONFIG.minimum_invoice_payment_sats,
            maximum_invoice_payment_sats=DEFAULT_CONFIG.maximum_invoice_payment_sats,
        )
    finally:
        if own:
            http.close()


def calculate_invoice_fees(sats: int, cfg: HiveInvoiceConfig) -> InvoiceFees:
    """conv_fee_percent * sats + conv_fee_sats, plus a fixed routing pad."""
    conv = (cfg.conv_fee_percent * Decimal(sats) + Decimal(cfg.conv_fee_sats)).to_integral_value(
        rounding=ROUND_HALF_UP
    )
    conv_i = int(conv)
    routing = cfg.routing_fee_sats
    return InvoiceFees(
        conv_fee_sats=conv_i,
        routing_fee_sats=routing,
        total_fee_sats=conv_i + routing,
        sats_collect=sats + conv_i + routing,
        conv_fee_percent=str(cfg.conv_fee_percent),
        conv_fee_base_sats=cfg.conv_fee_sats,
    )


def _parse_config(raw: dict, routing_fee_sats: int) -> HiveInvoiceConfig:
    windows = _parse_windows(raw.get("lightning_rate_limits") or [])
    if not windows:
        windows = list(DEFAULT_WINDOWS)
    percent = Decimal(str(raw.get("conv_fee_percent") or DEFAULT_CONFIG.conv_fee_percent))
    return HiveInvoiceConfig(
        windows=tuple(windows),
        conv_fee_percent=percent,
        conv_fee_sats=int(float(raw.get("conv_fee_sats") or DEFAULT_CONFIG.conv_fee_sats)),
        routing_fee_sats=routing_fee_sats,
        minimum_invoice_payment_sats=int(
            float(
                raw.get("minimum_invoice_payment_sats")
                or DEFAULT_CONFIG.minimum_invoice_payment_sats
            )
        ),
        maximum_invoice_payment_sats=int(
            float(
                raw.get("maximum_invoice_payment_sats")
                or DEFAULT_CONFIG.maximum_invoice_payment_sats
            )
        ),
    )


def _parse_windows(raw: list[object]) -> list[RateWindow]:
    windows: list[RateWindow] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        hours = int(item.get("hours") or 0)
        sats = int(float(item.get("sats") or 0))
        if hours > 0 and sats > 0:
            windows.append(RateWindow(hours=hours, sats=sats))
    windows.sort(key=lambda w: w.hours)
    return windows

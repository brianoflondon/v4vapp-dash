from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from v4vapp_dash.config import get_settings


@dataclass(frozen=True)
class RateWindow:
    hours: int
    sats: int


DEFAULT_WINDOWS: tuple[RateWindow, ...] = (
    RateWindow(hours=4, sats=600_000),
    RateWindow(hours=72, sats=1_200_000),
    RateWindow(hours=168, sats=2_000_000),
)

_cache: tuple[RateWindow, ...] | None = None
_cache_until: float = 0.0


def reset_rate_window_cache() -> None:
    global _cache, _cache_until
    _cache = None
    _cache_until = 0.0


def fetch_rate_windows(
    *, client: httpx.Client | None = None, force: bool = False
) -> list[RateWindow]:
    """Live Hive limits from GET /v1 (api.v4v.app). Falls back to last cache, then defaults."""
    global _cache, _cache_until
    now = time.monotonic()
    if not force and _cache is not None and now < _cache_until:
        return list(_cache)

    settings = get_settings()
    own = client is None
    http = client or httpx.Client(timeout=8.0)
    try:
        resp = http.get(settings.v4v_status_url)
        resp.raise_for_status()
        raw = (resp.json().get("config") or {}).get("lightning_rate_limits") or []
        windows = _parse_windows(raw)
        if not windows:
            raise ValueError("empty lightning_rate_limits")
        _cache, _cache_until = tuple(windows), now + 60
        return list(windows)
    except Exception:
        if _cache is not None:
            return list(_cache)
        return list(DEFAULT_WINDOWS)
    finally:
        if own:
            http.close()


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

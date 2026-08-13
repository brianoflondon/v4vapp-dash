import hmac

from fastapi import Header

from v4vapp_dash.api.errors import ApiError
from v4vapp_dash.config import get_settings


def _matches(provided: str, expected: str) -> bool:
    if not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


async def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> str:
    settings = get_settings()
    if not x_api_key:
        raise ApiError(401, "unauthorized", "Missing X-API-Key header")
    if _matches(x_api_key, settings.dash_api_key) or _matches(
        x_api_key, settings.dash_api_key_prev
    ):
        return x_api_key
    raise ApiError(401, "unauthorized", "Invalid API key")

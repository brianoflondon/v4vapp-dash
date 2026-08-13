from typing import Any

import httpx


class Blockbook:
    """Read-only fallback used by the watcher if dashd is down. Not required in v1."""

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def address_txs(self, address: str) -> dict[str, Any]:
        url = f"{self.base_url}/api/v2/address/{address}"
        response = await self._client.get(url, params={"details": "txs"})
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("Blockbook address payload is not an object")
        return body

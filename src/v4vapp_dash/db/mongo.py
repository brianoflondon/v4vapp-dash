from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

COL_INVOICES = "dash_invoices"
COL_WALLET_STATE = "dash_wallet_state"
COL_PAYOUTS = "dash_payouts"


class Mongo:
    """Async client matching backend DBConn flags. No import of v4vapp_backend_v2."""

    def __init__(self, uri: str, db_name: str, *, app_name: str = "v4vapp-dash") -> None:
        if not uri:
            raise ValueError("MONGO_URI is empty")
        self.uri = uri
        self.db_name = db_name
        self.client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            uri,
            tz_aware=True,
            retryWrites=True,
            retryReads=True,
            readPreference="primaryPreferred",
            w=1,
            journal=True,
            appName=app_name,
        )
        self.db: AsyncDatabase[dict[str, Any]] = self.client[db_name]

    async def ping(self) -> None:
        await self.client.admin.command("ping")

    async def close(self) -> None:
        await self.client.close()

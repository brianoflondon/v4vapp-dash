"""Hits the real rsPytest / v4vapp-dev on dot. Skipped when Mongo is unreachable."""

import os

import pytest
from pymongo.errors import ServerSelectionTimeoutError

from v4vapp_dash.db.indexes import ensure_indexes
from v4vapp_dash.db.mongo import COL_INVOICES, COL_PAYOUTS, COL_WALLET_STATE, Mongo

# Same replica set the backend uses (devhive / mongo-pytest-local). No auth.
DEFAULT_URI = os.environ.get(
    "V4VAPP_DASH_TEST_MONGO_URI",
    "mongodb://dot:37017/v4vapp-dev?replicaSet=rsPytest",
)
DB_NAME = os.environ.get("V4VAPP_DASH_TEST_MONGO_DB", "v4vapp-dev")


async def _mongo_or_skip() -> Mongo:
    mongo = Mongo(DEFAULT_URI, DB_NAME, app_name="v4vapp-dash-test")
    try:
        await mongo.ping()
    except ServerSelectionTimeoutError as exc:
        await mongo.close()
        pytest.skip(f"rsPytest not reachable: {exc}")
    return mongo


@pytest.mark.asyncio
async def test_ensure_indexes_on_v4vapp_dev() -> None:
    mongo = await _mongo_or_skip()
    try:
        created = await ensure_indexes(mongo.db)
        assert COL_INVOICES in created
        assert COL_PAYOUTS in created
        assert COL_WALLET_STATE in created
        invoice_names = await mongo.db[COL_INVOICES].index_information()
        assert "external_id" in invoice_names
        assert invoice_names["external_id"]["unique"] is True
        assert "address" in invoice_names
        payout_names = await mongo.db[COL_PAYOUTS].index_information()
        assert payout_names["txid"]["unique"] is True
        names = await mongo.db.list_collection_names()
        assert COL_INVOICES in names
        assert COL_WALLET_STATE in names
        assert COL_PAYOUTS in names
    finally:
        await mongo.close()

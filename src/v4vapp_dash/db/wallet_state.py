from datetime import UTC, datetime
from typing import Any

from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from v4vapp_dash.config import Network
from v4vapp_dash.db.mongo import COL_WALLET_STATE


class WalletStateMismatch(RuntimeError):
    """Configured xpub/fingerprint does not match the stored row for this network."""


async def ensure_wallet_state(
    db: AsyncDatabase[dict[str, Any]],
    *,
    network: Network,
    account_xpub: str,
    fingerprint: str,
    descriptor_range_end: int,
) -> dict[str, Any]:
    if not account_xpub:
        raise WalletStateMismatch("DASH_XPUB is required to initialize wallet state")
    if len(fingerprint) != 8:
        raise WalletStateMismatch("DASH_MASTER_FINGERPRINT must be 8 hex characters")

    coll = db[COL_WALLET_STATE]
    existing = await coll.find_one({"_id": network})
    now = datetime.now(UTC)
    if existing is None:
        doc: dict[str, Any] = {
            "_id": network,
            "network": network,
            "fingerprint": fingerprint.lower(),
            "account_xpub": account_xpub,
            "next_receive_index": 0,
            "next_change_index": 0,
            "descriptor_range_end": descriptor_range_end,
            "updated_at": now,
        }
        await coll.insert_one(doc)
        return doc

    stored_xpub = existing.get("account_xpub")
    stored_fp = str(existing.get("fingerprint", "")).lower()
    if stored_xpub != account_xpub or stored_fp != fingerprint.lower():
        raise WalletStateMismatch(
            f"dash_wallet_state[{network}] fingerprint/xpub does not match configured material"
        )
    if existing.get("network") != network:
        raise WalletStateMismatch(
            f"dash_wallet_state[{network}] network field {existing.get('network')!r} != _id"
        )
    return existing


async def allocate_receive_index(
    db: AsyncDatabase[dict[str, Any]], network: Network
) -> int:
    """Atomically take the current next_receive_index and increment it."""
    doc = await db[COL_WALLET_STATE].find_one_and_update(
        {"_id": network},
        {
            "$inc": {"next_receive_index": 1},
            "$set": {"updated_at": datetime.now(UTC)},
        },
        return_document=ReturnDocument.BEFORE,
    )
    if doc is None:
        raise WalletStateMismatch(f"dash_wallet_state[{network}] is missing")
    return int(doc["next_receive_index"])


async def load_wallet_state(
    db: AsyncDatabase[dict[str, Any]], network: Network
) -> dict[str, Any] | None:
    return await db[COL_WALLET_STATE].find_one({"_id": network})

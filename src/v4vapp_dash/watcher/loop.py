from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId

from v4vapp_dash.amounts import rpc_dash_to_duffs
from v4vapp_dash.config import Settings
from v4vapp_dash.dashd.rpc import Dashd, DashdError
from v4vapp_dash.db.mongo import COL_INVOICES
from v4vapp_dash.logging import logger
from v4vapp_dash.models.invoice import DashInvoiceState
from v4vapp_dash.watcher.settlement import (
    WATCH_STATES,
    Decision,
    WatchedOutput,
    apply_settlement,
    merge_outputs,
)


@dataclass
class WatcherState:
    last_tick_at: datetime | None = None
    last_error: str | None = None
    open_invoices: int = 0
    last_id: ObjectId | None = None
    ticks: int = 0
    stuck: int = 0


async def run_watcher(
    *,
    mongo: Any,
    dashd: Dashd,
    settings: Settings,
    state: WatcherState,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            await tick(mongo=mongo, dashd=dashd, settings=settings, state=state)
            state.last_error = None
        except Exception as exc:
            state.last_error = str(exc)
            logger.exception("watcher tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.dash_poll_interval_s)
        except TimeoutError:
            continue


async def tick(
    *,
    mongo: Any,
    dashd: Dashd,
    settings: Settings,
    state: WatcherState,
) -> None:
    invoices = await _load_batch(mongo, settings.dash_watch_batch, state.last_id)
    if len(invoices) < settings.dash_watch_batch:
        state.last_id = None
    elif invoices:
        state.last_id = invoices[-1]["_id"]
    watching = {DashInvoiceState.OPEN, DashInvoiceState.DETECTED}
    state.open_invoices = sum(1 for inv in invoices if inv.get("state") in watching)

    addresses = [inv["address"] for inv in invoices if inv.get("address")]
    utxos_by_addr: dict[str, list[dict[str, Any]]] = {addr: [] for addr in addresses}
    if addresses:
        try:
            raw = await dashd.listunspent(0, 9999999, addresses, True)
        except DashdError as exc:
            state.last_error = str(exc)
            state.last_tick_at = datetime.now(UTC)
            state.ticks += 1
            logger.error("listunspent failed: %s", exc)
            return
        for utxo in raw:
            addr = utxo.get("address")
            if addr in utxos_by_addr:
                utxos_by_addr[addr].append(utxo)

    now = datetime.now(UTC)
    for inv in invoices:
        utxos = utxos_by_addr.get(inv["address"], [])
        await _apply_invoice(mongo, dashd, settings, state, inv, utxos, now)

    state.last_tick_at = now
    state.ticks += 1


async def _load_batch(mongo: Any, limit: int, after: ObjectId | None) -> list[dict[str, Any]]:
    query: dict[str, Any] = {
        "swept_at": None,
        "state": {"$in": [s.value for s in WATCH_STATES]},
    }
    if after is not None:
        query["_id"] = {"$gt": after}
    cursor = mongo.db[COL_INVOICES].find(query).sort([("_id", 1)]).limit(limit)
    return [doc async for doc in cursor]


async def _apply_invoice(
    mongo: Any,
    dashd: Dashd,
    settings: Settings,
    watcher: WatcherState,
    inv: dict[str, Any],
    utxos: list[dict[str, Any]],
    now: datetime,
) -> None:
    fresh: list[WatchedOutput] = []
    for utxo in utxos:
        duffs = rpc_dash_to_duffs(utxo.get("amount", 0))
        if duffs < settings.dash_dust_duffs:
            continue
        out = await _enrich(dashd, utxo, duffs, now)
        fresh.append(out)

    existing_txids = list(inv.get("txids") or [])
    outputs = merge_outputs(existing_txids, fresh)
    prev = DashInvoiceState(inv["state"])
    decision = apply_settlement(
        state=prev,
        now=now,
        expires_at=inv["expires_at"],
        settle_deadline_at=inv["settle_deadline_at"],
        duffs_quoted=int(inv["duffs_quoted"]),
        sats_requested=int(inv["sats_requested"]),
        policy=inv.get("policy") or {},
        outputs=outputs,
        canceled=prev == DashInvoiceState.CANCELED,
    )
    if decision.stuck:
        watcher.stuck += 1
        logger.error("invoice %s stuck pending settlement past settle_deadline_at", inv.get("_id"))

    prev_received = int(inv.get("duffs_received") or 0)
    late_states = {DashInvoiceState.EXPIRED, DashInvoiceState.CANCELED}
    if prev in late_states and decision.duffs_received > prev_received:
        logger.error(
            "late payment on %s %s duffs_received=%s",
            prev.value,
            inv.get("address"),
            decision.duffs_received,
        )

    await _persist(mongo, inv, prev, decision, now)


async def _enrich(
    dashd: Dashd, utxo: dict[str, Any], duffs: int, now: datetime
) -> WatchedOutput:
    instantlock = bool(utxo.get("instantlock"))
    chainlock = bool(utxo.get("chainlock"))
    confirmations = int(utxo.get("confirmations") or 0)
    try:
        tx = await dashd.gettransaction(str(utxo["txid"]))
        instantlock = bool(tx.get("instantlock"))
        chainlock = bool(tx.get("chainlock"))
        confirmations = int(tx.get("confirmations") or confirmations)
    except DashdError:
        pass
    return WatchedOutput(
        txid=str(utxo["txid"]),
        vout=int(utxo["vout"]),
        duffs=duffs,
        confirmations=confirmations,
        instantlock=instantlock,
        chainlock=chainlock,
        detected_at=now,
    )


async def _persist(
    mongo: Any,
    inv: dict[str, Any],
    prev: DashInvoiceState,
    decision: Decision,
    now: datetime,
) -> None:
    allowed = {prev.value}
    if decision.state in {DashInvoiceState.SETTLED, DashInvoiceState.OVERPAID}:
        allowed = {DashInvoiceState.OPEN.value, DashInvoiceState.DETECTED.value}
    elif decision.state == DashInvoiceState.UNDERPAID:
        allowed = {DashInvoiceState.OPEN.value, DashInvoiceState.DETECTED.value}
    elif decision.state == DashInvoiceState.EXPIRED:
        allowed = {DashInvoiceState.OPEN.value}
    elif decision.state == DashInvoiceState.DETECTED:
        allowed = {DashInvoiceState.OPEN.value, DashInvoiceState.DETECTED.value}

    update = {
        "state": decision.state.value,
        "first_seen_at": decision.first_seen_at,
        "detected_at": decision.detected_at,
        "settled_at": decision.settled_at,
        "duffs_received": decision.duffs_received,
        "sats_credited": decision.sats_credited,
        "late_payment": decision.late_payment,
        "txids": decision.txids,
        "updated_at": now,
    }
    filt: dict[str, Any] = {"_id": inv["_id"], "state": {"$in": list(allowed)}}
    if decision.state in {DashInvoiceState.EXPIRED, DashInvoiceState.CANCELED} and prev in {
        DashInvoiceState.EXPIRED,
        DashInvoiceState.CANCELED,
    }:
        filt = {"_id": inv["_id"], "state": prev.value}

    await mongo.db[COL_INVOICES].find_one_and_update(filt, {"$set": update})

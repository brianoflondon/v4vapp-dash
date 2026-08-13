from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, Query, Request, Response
from pymongo.errors import DuplicateKeyError

from v4vapp_dash.api.deps import require_api_key
from v4vapp_dash.api.errors import ApiError
from v4vapp_dash.config import Settings, default_min_conf, get_settings
from v4vapp_dash.db.mongo import COL_INVOICES, Mongo
from v4vapp_dash.db.wallet_state import WalletStateMismatch, allocate_receive_index
from v4vapp_dash.keys import load_xpub_material
from v4vapp_dash.limits.check import check_cust_rate_limit
from v4vapp_dash.limits.hive_config import calculate_invoice_fees, fetch_hive_config
from v4vapp_dash.models.invoice import (
    DashInvoiceState,
    InvoiceCreate,
    InvoiceListOut,
    InvoiceOut,
    doc_to_out,
    payment_uri,
)
from v4vapp_dash.quotes.service import fetch_quote, quote_for_sats
from v4vapp_dash.wallet.hd import derive_receive

router = APIRouter(prefix="/v1/invoices", tags=["invoices"])


def _mongo(request: Request) -> Mongo:
    mongo = getattr(request.app.state, "mongo", None)
    if mongo is None:
        raise ApiError(503, "wallet_unavailable", "Mongo is not configured")
    return mongo


def _material_or_503(settings: Settings) -> Any:
    material = load_xpub_material(settings)
    if material is None:
        raise ApiError(503, "wallet_unavailable", "DASH_XPUB is not configured")
    return material


def _policy(settings: Settings, min_confirmations: int | None) -> dict[str, Any]:
    is_or_cl = settings.dash_settle_policy == "instantsend_or_chainlock"
    if is_or_cl:
        min_conf = default_min_conf(settings.dash_network)
    else:
        min_conf = min_confirmations if min_confirmations is not None else settings.dash_min_conf
    return {
        "settle_policy": settings.dash_settle_policy,
        "underpay_tolerance_duffs": settings.dash_underpay_duffs,
        "underpay_tolerance_bps": settings.dash_underpay_bps,
        "min_confirmations": min_conf,
        "accept_instantsend": is_or_cl,
        "accept_chainlock": is_or_cl,
    }


@router.post("", status_code=201)
async def create_invoice(
    body: InvoiceCreate,
    request: Request,
    response: Response,
    _key: str = Depends(require_api_key),
) -> InvoiceOut:
    settings = get_settings()
    mongo = _mongo(request)
    material = _material_or_503(settings)

    existing = await mongo.db[COL_INVOICES].find_one({"external_id": body.external_id})
    if existing is not None:
        same = (
            int(existing["sats_requested"]) == body.sats
            and int(existing.get("expires_in_s", -1)) == body.expires_in_s
        )
        if same:
            response.status_code = 200
            return doc_to_out(existing)
        raise ApiError(
            409, "duplicate_external_id", "external_id already used with different params"
        )

    hive = fetch_hive_config()
    if body.sats < hive.minimum_invoice_payment_sats:
        raise ApiError(
            422,
            "amount_too_small",
            f"Minimum invoice is {hive.minimum_invoice_payment_sats:,} sats",
        )
    if body.sats > hive.maximum_invoice_payment_sats:
        raise ApiError(
            422,
            "amount_too_large",
            f"Maximum invoice is {hive.maximum_invoice_payment_sats:,} sats",
        )

    fees = calculate_invoice_fees(body.sats, hive)
    try:
        raw_quote = fetch_quote()
        priced = quote_for_sats(fees.sats_collect, raw_quote)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(422, "quote_unavailable", str(exc)) from exc

    if body.cust_id:
        await check_cust_rate_limit(mongo.db, cust_id=body.cust_id, extra_sats=body.sats)

    try:
        index = await allocate_receive_index(mongo.db, settings.dash_network)
    except WalletStateMismatch as exc:
        raise ApiError(503, "wallet_unavailable", str(exc)) from exc

    if index >= settings.dash_descriptor_range_end:
        raise ApiError(503, "index_exhausted", "receive index is past the descriptor range")

    address, derivation = derive_receive(material.account_xpub, settings.dash_network, index)
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=body.expires_in_s)
    settle_deadline_at = expires_at + timedelta(seconds=settings.dash_settle_grace_s)
    doc: dict[str, Any] = {
        "external_id": body.external_id,
        "cust_id": body.cust_id,
        "memo": body.memo,
        "state": DashInvoiceState.OPEN.value,
        "address": address,
        "uri": payment_uri(address, priced.dash_quoted),
        "network": settings.dash_network,
        "path": derivation.path,
        "account": derivation.account,
        "change": derivation.change,
        "index": index,
        "derivation": derivation.model_dump(),
        "sats_requested": body.sats,
        "sats_collect": fees.sats_collect,
        "expires_in_s": body.expires_in_s,
        "duffs_quoted": priced.duffs_quoted,
        "dash_quoted": priced.dash_quoted,
        "fees": {
            "conv_fee_percent": fees.conv_fee_percent,
            "conv_fee_base_sats": fees.conv_fee_base_sats,
            "conv_fee_sats": fees.conv_fee_sats,
            "routing_fee_sats": fees.routing_fee_sats,
            "total_fee_sats": fees.total_fee_sats,
            "sats_collect": fees.sats_collect,
        },
        "duffs_received": 0,
        "sats_credited": None,
        "quote": {
            "source": priced.source,
            "fetched_at": priced.fetched_at,
            "btc_usd": str(priced.btc_usd),
            "dash_usd": str(priced.dash_usd),
            "dash_btc": str(priced.dash_btc),
            "sats_per_dash": str(priced.sats_per_dash),
            "ttl_s": priced.ttl_s,
        },
        "policy": _policy(settings, body.min_confirmations),
        "txids": [],
        "created_at": now,
        "expires_at": expires_at,
        "settle_deadline_at": settle_deadline_at,
        "first_seen_at": None,
        "detected_at": None,
        "settled_at": None,
        "canceled_at": None,
        "late_payment": False,
        "swept_at": None,
        "updated_at": now,
    }
    try:
        result = await mongo.db[COL_INVOICES].insert_one(doc)
    except DuplicateKeyError as exc:
        raise ApiError(409, "duplicate_external_id", "external_id already exists") from exc
    doc["_id"] = result.inserted_id
    return doc_to_out(doc)


def _as_object_id(invoice_id: str) -> ObjectId:
    try:
        return ObjectId(invoice_id)
    except InvalidId as exc:
        raise ApiError(404, "not_found", "invoice not found") from exc


@router.get("/by-external/{external_id:path}")
async def get_by_external(
    external_id: str,
    request: Request,
    _key: str = Depends(require_api_key),
) -> InvoiceOut:
    mongo = _mongo(request)
    doc = await mongo.db[COL_INVOICES].find_one({"external_id": external_id})
    if doc is None:
        raise ApiError(404, "not_found", "invoice not found")
    return doc_to_out(doc)


@router.get("/{invoice_id}")
async def get_invoice(
    invoice_id: str,
    request: Request,
    _key: str = Depends(require_api_key),
) -> InvoiceOut:
    mongo = _mongo(request)
    doc = await mongo.db[COL_INVOICES].find_one({"_id": _as_object_id(invoice_id)})
    if doc is None:
        raise ApiError(404, "not_found", "invoice not found")
    return doc_to_out(doc)


@router.get("")
async def list_invoices(
    request: Request,
    _key: str = Depends(require_api_key),
    state: DashInvoiceState | None = None,
    cust_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> InvoiceListOut:
    mongo = _mongo(request)
    query: dict[str, Any] = {}
    if state is not None:
        query["state"] = state.value
    if cust_id is not None:
        query["cust_id"] = cust_id
    if cursor:
        created_raw, id_raw = _split_cursor(cursor)
        oid = _as_object_id(id_raw)
        query["$or"] = [
            {"created_at": {"$gt": created_raw}},
            {"created_at": created_raw, "_id": {"$gt": oid}},
        ]
    cursor_docs = (
        mongo.db[COL_INVOICES]
        .find(query)
        .sort([("created_at", 1), ("_id", 1)])
        .limit(limit + 1)
    )
    rows = [doc async for doc in cursor_docs]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        rows = rows[:limit]
        next_cursor = f"{last['created_at'].isoformat()}|{last['_id']}"
    return InvoiceListOut(items=[doc_to_out(doc) for doc in rows], next_cursor=next_cursor)


@router.post("/{invoice_id}/cancel")
async def cancel_invoice(
    invoice_id: str,
    request: Request,
    _key: str = Depends(require_api_key),
) -> InvoiceOut:
    mongo = _mongo(request)
    now = datetime.now(UTC)
    doc = await mongo.db[COL_INVOICES].find_one_and_update(
        {
            "_id": _as_object_id(invoice_id),
            "state": DashInvoiceState.OPEN.value,
            "first_seen_at": None,
        },
        {"$set": {"state": DashInvoiceState.CANCELED.value, "canceled_at": now, "updated_at": now}},
        return_document=True,
    )
    if doc is None:
        existing = await mongo.db[COL_INVOICES].find_one({"_id": _as_object_id(invoice_id)})
        if existing is None:
            raise ApiError(404, "not_found", "invoice not found")
        raise ApiError(409, "invoice_not_cancelable", "invoice is not OPEN or already has funds")
    return doc_to_out(doc)


def _split_cursor(cursor: str) -> tuple[datetime, str]:
    if "|" not in cursor:
        raise ApiError(400, "invalid_request", "cursor must be created_at|_id")
    created_raw, id_raw = cursor.split("|", 1)
    try:
        created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApiError(400, "invalid_request", "cursor timestamp is invalid") from exc
    return created, id_raw

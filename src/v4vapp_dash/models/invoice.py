from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from v4vapp_dash.config import Network, SettlePolicy
from v4vapp_dash.models.wallet import Derivation


class DashInvoiceState(StrEnum):
    OPEN = "OPEN"
    DETECTED = "DETECTED"
    SETTLED = "SETTLED"
    UNDERPAID = "UNDERPAID"
    OVERPAID = "OVERPAID"
    EXPIRED = "EXPIRED"
    CANCELED = "CANCELED"


class InvoiceFeesOut(BaseModel):
    conv_fee_percent: str
    conv_fee_base_sats: int
    conv_fee_sats: int
    routing_fee_sats: int
    total_fee_sats: int
    sats_collect: int


class InvoiceCreate(BaseModel):
    external_id: str = Field(min_length=1, max_length=128)
    sats: int = Field(ge=1)
    expires_in_s: int = Field(ge=60, le=86_400)
    cust_id: str | None = None
    memo: str | None = Field(default=None, max_length=300)
    min_confirmations: int | None = Field(default=None, ge=1, le=100)


class QuoteSnapshot(BaseModel):
    source: str
    fetched_at: str
    btc_usd: str
    dash_usd: str
    dash_btc: str
    sats_per_dash: str
    ttl_s: int


class InvoicePolicy(BaseModel):
    settle_policy: SettlePolicy
    underpay_tolerance_duffs: int
    underpay_tolerance_bps: int
    min_confirmations: int
    accept_instantsend: bool
    accept_chainlock: bool


class InvoiceTx(BaseModel):
    txid: str
    vout: int
    duffs: int
    confirmations: int
    instantlock: bool
    chainlock: bool
    detected_at: datetime


class InvoiceOut(BaseModel):
    invoice_id: str
    external_id: str
    state: DashInvoiceState
    address: str
    uri: str
    network: Network
    sats_requested: int
    sats_collect: int | None = None
    duffs_quoted: int
    dash_quoted: str
    fees: InvoiceFeesOut | None = None
    duffs_received: int = 0
    sats_credited: int | None = None
    expires_at: datetime
    settle_deadline_at: datetime
    created_at: datetime
    first_seen_at: datetime | None = None
    detected_at: datetime | None = None
    settled_at: datetime | None = None
    canceled_at: datetime | None = None
    expired: bool = False
    late_payment: bool = False
    quote: QuoteSnapshot
    derivation: Derivation
    policy: InvoicePolicy
    txids: list[InvoiceTx] = Field(default_factory=list)
    cust_id: str | None = None
    memo: str | None = None


class InvoiceListOut(BaseModel):
    items: list[InvoiceOut]
    next_cursor: str | None = None


def payment_uri(address: str, dash_quoted: str) -> str:
    return f"dash:{address}?amount={dash_quoted}"


def doc_to_out(doc: dict[str, Any]) -> InvoiceOut:
    quote = doc["quote"]
    der = doc["derivation"]
    policy = doc["policy"]
    state = DashInvoiceState(doc["state"])
    return InvoiceOut(
        invoice_id=str(doc["_id"]),
        external_id=doc["external_id"],
        state=state,
        address=doc["address"],
        uri=doc["uri"],
        network=doc["network"],
        sats_requested=int(doc["sats_requested"]),
        sats_collect=int(doc["sats_collect"]) if doc.get("sats_collect") is not None else None,
        duffs_quoted=int(doc["duffs_quoted"]),
        dash_quoted=doc["dash_quoted"],
        fees=InvoiceFeesOut.model_validate(doc["fees"]) if doc.get("fees") else None,
        duffs_received=int(doc.get("duffs_received") or 0),
        sats_credited=doc.get("sats_credited"),
        expires_at=doc["expires_at"],
        settle_deadline_at=doc["settle_deadline_at"],
        created_at=doc["created_at"],
        first_seen_at=doc.get("first_seen_at"),
        detected_at=doc.get("detected_at"),
        settled_at=doc.get("settled_at"),
        canceled_at=doc.get("canceled_at"),
        expired=state == DashInvoiceState.EXPIRED,
        late_payment=bool(doc.get("late_payment")),
        quote=QuoteSnapshot(
            source=quote["source"],
            fetched_at=quote["fetched_at"],
            btc_usd=str(quote["btc_usd"]),
            dash_usd=str(quote["dash_usd"]),
            dash_btc=str(quote["dash_btc"]),
            sats_per_dash=str(quote["sats_per_dash"]),
            ttl_s=int(quote["ttl_s"]),
        ),
        derivation=Derivation(
            account=int(der["account"]),
            change=int(der["change"]),
            index=int(der["index"]),
            path=der["path"],
        ),
        policy=InvoicePolicy(
            settle_policy=policy["settle_policy"],
            underpay_tolerance_duffs=int(policy["underpay_tolerance_duffs"]),
            underpay_tolerance_bps=int(policy["underpay_tolerance_bps"]),
            min_confirmations=int(policy["min_confirmations"]),
            accept_instantsend=bool(policy["accept_instantsend"]),
            accept_chainlock=bool(policy["accept_chainlock"]),
        ),
        txids=[InvoiceTx.model_validate(tx) for tx in doc.get("txids") or []],
        cust_id=doc.get("cust_id"),
        memo=doc.get("memo"),
    )

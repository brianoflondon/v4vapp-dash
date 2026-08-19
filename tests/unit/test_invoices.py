import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from v4vapp_dash.config import get_settings
from v4vapp_dash.db.mongo import COL_INVOICES, COL_WALLET_STATE
from v4vapp_dash.limits.hive_config import DEFAULT_CONFIG
from v4vapp_dash.main import create_app
from v4vapp_dash.models.invoice import DashInvoiceState
from v4vapp_dash.models.quote import Quote
from v4vapp_dash.quotes.service import quote_for_sats

TESTNET_XPUB = (
    "tpubDC5FSnBiZDMmhiuCmWAYsLwgLYrrT9rAqvTySfuCCrgsWz8wxMXUS9Tb9iVMvcRbv"
    "FcAHGkMD5Kx8koh4GquNGNTfohfk7pgjhaPCdXpoba"
)
FP = "73c5da0a"
HEADERS = {"X-API-Key": "test-api-key"}


class _InsertOne:
    def __init__(self, inserted_id: ObjectId) -> None:
        self.inserted_id = inserted_id


class _Coll:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    def _match(self, doc: dict[str, Any], query: dict[str, Any]) -> bool:
        for key, value in query.items():
            if key == "$or":
                if not any(self._match(doc, part) for part in value):
                    return False
                continue
            if isinstance(value, dict):
                actual = doc.get(key)
                if "$gt" in value and not (actual is not None and actual > value["$gt"]):
                    return False
                if "$gte" in value and not (actual is not None and actual >= value["$gte"]):
                    return False
                if "$eq" in value and actual != value["$eq"]:
                    return False
                if "$in" in value and actual not in value["$in"]:
                    return False
                continue
            if doc.get(key) != value:
                return False
        return True

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self.docs:
            if self._match(doc, query):
                return dict(doc)
        return None

    async def insert_one(self, doc: dict[str, Any]) -> _InsertOne:
        if any(existing.get("external_id") == doc.get("external_id") for existing in self.docs):
            raise DuplicateKeyError("E11000 duplicate external_id")
        stored = dict(doc)
        stored["_id"] = ObjectId()
        self.docs.append(stored)
        return _InsertOne(stored["_id"])

    async def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        return_document: Any = None,
    ) -> dict[str, Any] | None:
        for doc in self.docs:
            if not self._match(doc, query):
                continue
            before = dict(doc)
            for key, value in update.get("$inc", {}).items():
                doc[key] = int(doc.get(key, 0)) + int(value)
            doc.update(update.get("$set", {}))
            return dict(doc) if return_document else before
        return None

    def find(self, query: dict[str, Any]) -> "_Cursor":
        matched = [dict(doc) for doc in self.docs if self._match(doc, query)]
        return _Cursor(matched)


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def sort(self, spec: list[tuple[str, int]]) -> "_Cursor":
        for key, direction in reversed(spec):
            self._rows.sort(key=lambda row: row[key], reverse=direction < 0)
        return self

    def limit(self, n: int) -> "_Cursor":
        self._rows = self._rows[:n]
        return self

    def __aiter__(self) -> "_Cursor":
        self._iter = iter(self._rows)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _Db:
    def __init__(self) -> None:
        self.cols = {COL_INVOICES: _Coll(), COL_WALLET_STATE: _Coll()}

    def __getitem__(self, name: str) -> _Coll:
        return self.cols[name]


class _Mongo:
    def __init__(self) -> None:
        self.db = _Db()


def _quote() -> Quote:
    return Quote(
        source="coingecko",
        fetched_at="2026-08-13T12:00:00Z",
        btc_usd=Decimal("65000"),
        dash_usd=Decimal("32.5"),
        dash_btc=Decimal("0.0005"),
        sats_per_dash=Decimal("50000"),
        ttl_s=60,
        duffs_quoted=2000,
        dash_quoted="0.00002000",
    )


@pytest.fixture
def invoice_client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, _Mongo]:
    monkeypatch.setenv("DASH_XPUB", TESTNET_XPUB)
    monkeypatch.setenv("DASH_MASTER_FINGERPRINT", FP)
    monkeypatch.setenv("DASH_NETWORK", "testnet")
    monkeypatch.setenv("DASH_SETTLE_POLICY", "instantsend_or_chainlock")
    get_settings.cache_clear()
    monkeypatch.setattr("v4vapp_dash.api.v1.invoices.fetch_quote", lambda: _quote())
    monkeypatch.setattr(
        "v4vapp_dash.api.v1.invoices.fetch_hive_config",
        lambda: DEFAULT_CONFIG,
    )
    monkeypatch.setattr(
        "v4vapp_dash.limits.check.fetch_rate_windows",
        lambda: list(DEFAULT_CONFIG.windows),
    )

    mongo = _Mongo()
    mongo.db[COL_WALLET_STATE].docs.append(
        {
            "_id": "testnet",
            "network": "testnet",
            "fingerprint": FP,
            "account_xpub": TESTNET_XPUB,
            "next_receive_index": 0,
            "next_change_index": 0,
            "descriptor_range_end": 100000,
            "updated_at": datetime.now(UTC),
        }
    )
    monkeypatch.setattr("v4vapp_dash.api.v1.invoices._mongo", lambda _request: mongo)
    client = TestClient(create_app())
    return client, mongo


def test_create_invoice_returns_y_address_and_quoted_duffs(
    invoice_client: tuple[TestClient, _Mongo],
) -> None:
    client, mongo = invoice_client
    response = client.post(
        "/v1/invoices",
        headers=HEADERS,
        json={
            "external_id": "hive:test:1",
            "sats": 25000,
            "expires_in_s": 900,
            "cust_id": "v4vapp-test",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["state"] == "OPEN"
    assert body["address"].startswith("y")
    # 25000 + ceil/half-up(25000*0.029)+50 + 300 = 25000+725+50+300 = 26075 sats collected
    assert body["sats_requested"] == 25_000
    assert body["sats_collect"] == 26_075
    assert body["fees"]["total_fee_sats"] == 1_075
    assert body["fees"]["routing_fee_sats"] == 300
    assert body["duffs_quoted"] == 52_150_000
    assert body["uri"].startswith("dash:y")
    assert body["derivation"]["path"] == "m/44'/1'/0'/0/0"
    assert body["policy"]["settle_policy"] == "instantsend_or_chainlock"
    assert body["policy"]["accept_instantsend"] is True
    assert mongo.db[COL_WALLET_STATE].docs[0]["next_receive_index"] == 1


def test_create_stores_lightning_invoice(
    invoice_client: tuple[TestClient, _Mongo],
) -> None:
    client, mongo = invoice_client
    bolt11 = "lnbc250u1ptestinvoice"
    response = client.post(
        "/v1/invoices",
        headers=HEADERS,
        json={
            "external_id": "hive:ln:1",
            "sats": 25000,
            "expires_in_s": 900,
            "lightning_invoice": bolt11,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["lightning_invoice"] == bolt11
    stored = mongo.db[COL_INVOICES].docs[0]
    assert stored["lightning_invoice"] == bolt11


def test_create_is_idempotent_for_same_payload(
    invoice_client: tuple[TestClient, _Mongo],
) -> None:
    client, _mongo = invoice_client
    payload = {"external_id": "hive:test:dup", "sats": 1000, "expires_in_s": 120}
    first = client.post("/v1/invoices", headers=HEADERS, json=payload)
    second = client.post("/v1/invoices", headers=HEADERS, json=payload)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["invoice_id"] == second.json()["invoice_id"]
    assert first.json()["address"] == second.json()["address"]


def test_create_conflict_on_different_payload(
    invoice_client: tuple[TestClient, _Mongo],
) -> None:
    client, _mongo = invoice_client
    client.post(
        "/v1/invoices",
        headers=HEADERS,
        json={"external_id": "hive:test:x", "sats": 1000, "expires_in_s": 120},
    )
    response = client.post(
        "/v1/invoices",
        headers=HEADERS,
        json={"external_id": "hive:test:x", "sats": 2000, "expires_in_s": 120},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_external_id"


def test_get_and_cancel(invoice_client: tuple[TestClient, _Mongo]) -> None:
    client, _mongo = invoice_client
    created = client.post(
        "/v1/invoices",
        headers=HEADERS,
        json={"external_id": "hive:test:cancel", "sats": 1000, "expires_in_s": 120},
    ).json()
    fetched = client.get(f"/v1/invoices/{created['invoice_id']}", headers=HEADERS)
    assert fetched.status_code == 200
    by_ext = client.get("/v1/invoices/by-external/hive:test:cancel", headers=HEADERS)
    assert by_ext.json()["address"] == created["address"]
    canceled = client.post(f"/v1/invoices/{created['invoice_id']}/cancel", headers=HEADERS)
    assert canceled.status_code == 200
    assert canceled.json()["state"] == "CANCELED"
    again = client.post(f"/v1/invoices/{created['invoice_id']}/cancel", headers=HEADERS)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "invoice_not_cancelable"


def test_list_and_auth(invoice_client: tuple[TestClient, _Mongo]) -> None:
    client, _mongo = invoice_client
    assert client.get("/v1/invoices").status_code == 401
    client.post(
        "/v1/invoices",
        headers=HEADERS,
        json={"external_id": "a", "sats": 1000, "expires_in_s": 120},
    )
    listed = client.get("/v1/invoices?state=OPEN", headers=HEADERS)
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1


def test_payouts_are_501(invoice_client: tuple[TestClient, _Mongo]) -> None:
    client, _mongo = invoice_client
    response = client.post("/v1/payouts", headers=HEADERS, json={})
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "not_implemented"


def test_quote_math_still_matches_create() -> None:
    priced = quote_for_sats(25_000, _quote())
    assert priced.duffs_quoted == 50_000_000


def test_rejects_above_hive_maximum(invoice_client: tuple[TestClient, _Mongo]) -> None:
    client, _mongo = invoice_client
    response = client.post(
        "/v1/invoices",
        headers=HEADERS,
        json={"external_id": "hive:test:max", "sats": 180_001, "expires_in_s": 120},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "amount_too_large"


def test_rate_limit_rejects_when_paid_sats_exceed_window(
    invoice_client: tuple[TestClient, _Mongo],
) -> None:
    client, mongo = invoice_client
    mongo.db[COL_INVOICES].docs.append(
        {
            "_id": ObjectId(),
            "cust_id": "wallet-app-1",
            "state": DashInvoiceState.SETTLED.value,
            "sats_requested": 500_000,
            "sats_credited": 500_000,
            "settled_at": datetime.now(UTC),
        }
    )
    response = client.post(
        "/v1/invoices",
        headers=HEADERS,
        json={
            "external_id": "hive:test:over",
            "sats": 150_000,
            "expires_in_s": 120,
            "cust_id": "wallet-app-1",
        },
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "rate_limit_exceeded"
    assert "exceeded" in body["error"]["message"]
    check = body["limit_check"]
    assert check["cust_id"] == "wallet-app-1"
    assert check["limit_ok"] is False
    assert check["periods"]["4"]["sats"] == 650_000
    assert check["periods"]["4"]["limit_sats"] == 600_000
    assert check["periods"]["4"]["limit_ok"] is False


def test_rate_limit_allows_under_window(
    invoice_client: tuple[TestClient, _Mongo],
) -> None:
    client, _mongo = invoice_client
    response = client.post(
        "/v1/invoices",
        headers=HEADERS,
        json={
            "external_id": "hive:test:under",
            "sats": 25_000,
            "expires_in_s": 120,
            "cust_id": "wallet-app-2",
        },
    )
    assert response.status_code == 201, response.text


def _request_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "v4vapp_dash" and r.getMessage() == "request"]


def test_get_invoice_request_is_not_info(
    invoice_client: tuple[TestClient, _Mongo], caplog: pytest.LogCaptureFixture
) -> None:
    client, _mongo = invoice_client
    created = client.post(
        "/v1/invoices",
        headers=HEADERS,
        json={"external_id": "hive:log:get", "sats": 1000, "expires_in_s": 120},
    )
    assert created.status_code == 201
    invoice_id = created.json()["invoice_id"]
    path = f"/v1/invoices/{invoice_id}"
    caplog.set_level(logging.DEBUG, logger="v4vapp_dash")
    caplog.clear()
    response = client.get(path, headers=HEADERS)
    assert response.status_code == 200
    reqs = _request_records(caplog)
    assert [r for r in reqs if r.levelno >= logging.INFO] == []
    assert any(r.levelno == logging.DEBUG and getattr(r, "path", None) == path for r in reqs)


def test_get_by_external_request_is_not_info(
    invoice_client: tuple[TestClient, _Mongo], caplog: pytest.LogCaptureFixture
) -> None:
    client, _mongo = invoice_client
    created = client.post(
        "/v1/invoices",
        headers=HEADERS,
        json={"external_id": "hive:log:ext", "sats": 1000, "expires_in_s": 120},
    )
    assert created.status_code == 201
    path = "/v1/invoices/by-external/hive:log:ext"
    caplog.set_level(logging.DEBUG, logger="v4vapp_dash")
    caplog.clear()
    response = client.get(path, headers=HEADERS)
    assert response.status_code == 200
    reqs = _request_records(caplog)
    assert [r for r in reqs if r.levelno >= logging.INFO] == []
    assert any(r.levelno == logging.DEBUG and getattr(r, "path", None) == path for r in reqs)


def test_list_invoices_request_is_info(
    invoice_client: tuple[TestClient, _Mongo], caplog: pytest.LogCaptureFixture
) -> None:
    client, _mongo = invoice_client
    created = client.post(
        "/v1/invoices",
        headers=HEADERS,
        json={"external_id": "hive:log:list", "sats": 1000, "expires_in_s": 120},
    )
    assert created.status_code == 201
    caplog.set_level(logging.INFO, logger="v4vapp_dash")
    caplog.clear()
    response = client.get("/v1/invoices", headers=HEADERS)
    assert response.status_code == 200
    assert any(
        r.levelno == logging.INFO
        and getattr(r, "path", None) == "/v1/invoices"
        and getattr(r, "status", None) == 200
        for r in _request_records(caplog)
    )


def test_post_create_emits_info_request(
    invoice_client: tuple[TestClient, _Mongo], caplog: pytest.LogCaptureFixture
) -> None:
    client, _mongo = invoice_client
    caplog.set_level(logging.INFO, logger="v4vapp_dash")
    caplog.clear()
    response = client.post(
        "/v1/invoices",
        headers=HEADERS,
        json={
            "external_id": "hive:log:create",
            "sats": 25000,
            "expires_in_s": 900,
            "cust_id": "alice",
        },
    )
    assert response.status_code == 201
    invoice_id = response.json()["invoice_id"]
    reqs = _request_records(caplog)
    access = [
        r
        for r in reqs
        if r.levelno == logging.INFO
        and getattr(r, "status", None) == 201
        and getattr(r, "path", None) == "/v1/invoices"
        and getattr(r, "method", None) == "POST"
    ]
    assert access
    rec = access[-1]
    assert rec.invoice_id == invoice_id
    assert rec.external_id == "hive:log:create"
    assert rec.cust_id == "alice"
    assert not hasattr(rec, "address")
    created = [r for r in caplog.records if r.getMessage() == "invoice created"]
    assert created
    rec = created[-1]
    assert rec.levelno == logging.INFO
    assert rec.invoice_id == invoice_id
    assert rec.external_id == "hive:log:create"
    assert rec.cust_id == "alice"
    assert rec.sats == 25_000
    assert rec.state == "OPEN"
    assert rec.address == response.json()["address"]


def test_cancel_emits_invoice_canceled(
    invoice_client: tuple[TestClient, _Mongo], caplog: pytest.LogCaptureFixture
) -> None:
    client, _mongo = invoice_client
    created = client.post(
        "/v1/invoices",
        headers=HEADERS,
        json={"external_id": "hive:log:cancel", "sats": 1000, "expires_in_s": 120},
    ).json()
    caplog.set_level(logging.INFO, logger="v4vapp_dash")
    caplog.clear()
    response = client.post(f"/v1/invoices/{created['invoice_id']}/cancel", headers=HEADERS)
    assert response.status_code == 200
    canceled = [r for r in caplog.records if r.getMessage() == "invoice canceled"]
    assert canceled
    rec = canceled[-1]
    assert rec.invoice_id == created["invoice_id"]
    assert rec.external_id == "hive:log:cancel"

# v4vapp-dash

Private Dash invoice bridge for [V4V.app](https://v4v.app). Own GitHub repo, own Docker image. The only caller is `v4vapp-backend-v2` on an internal Docker / Tailscale network. This service does **not** publish a public port.

Design: [`docs/design-dash-integration-bridge.md`](docs/design-dash-integration-bridge.md).

v1 (in progress): watch-only receive invoices. Outgoing payouts are designed but return 501 until a later series.

## Stack

Python 3.12, FastAPI, `bip_utils` for BIP44 (coin type 5 / testnet 1), later `dashd` JSON-RPC. Not the Dash Platform JS SDK.

## Local

```bash
cp example.env .env          # placeholders only — never commit .env
uv sync --dev
uv run pytest tests/unit
uv run uvicorn v4vapp_dash.main:app --reload --port 8088
```

`GET /health` is open. `/metrics` and `/v1/*` need `X-API-Key`.

```bash
curl -s localhost:8088/health
curl -s -H "X-API-Key: change-me-long-random" localhost:8088/metrics
```

Docker (no host ports):

```bash
docker compose up -d --build
```

Local port 8088:

```bash
docker compose -f docker-compose.yaml -f docker-compose.regtest.yaml up --build
```

## Mongo

Shares the backend test database `v4vapp-dev` on replica set `rsPytest` (`mongo-pytest-local` on dot). That replica set has **no auth**. Connection strings match backend YAML:

| Where this process runs | `MONGO_URI` (from backend config) |
|---|---|
| Host on dot (`devhive.config.yaml`) | `mongodb://dot:37017/v4vapp-dev?replicaSet=rsPytest` |
| Other Docker stack (`devdocker.config.yaml`) | `mongodb://dot.tail400e5.ts.net:37017/v4vapp-dev?replicaSet=rsPytest` |

Startup creates `dash_invoices`, `dash_wallet_state`, `dash_payouts` and their indexes. It does not write `ledger` / `invoices`. Wallet state is one document per `DASH_NETWORK`; boot aborts if the stored xpub/fingerprint disagrees with env.

```bash
uv run pytest tests/unit tests/integration
```

The live integration test is skipped when `rsPytest` is unreachable (GitHub CI).

## dashd

Node RPCs (`getblockchaininfo`) go to `DASH_RPC_URL`. Wallet RPCs go to `DASH_RPC_URL/wallet/watch`.

Yoga (`deploy/dashd`) currently finishes IBD with wallet **disabled**. `getblockchaininfo` works today. Creating the watch-only wallet needs `-disablewallet=0` and a recreate (no resync), plus `DASH_XPUB` + `DASH_MASTER_FINGERPRINT`.

Until those are set, `/health` reports `dashd.synced` / `initialblockdownload` and skips wallet bootstrap.

## Offline xpub (laptop only)

```bash
uv run python -m v4vapp_dash.wallet.derive_xpub --network mainnet
# paste mnemonic on stdin; prints {network, account_xpub, master_fingerprint}
```

Put that JSON in `secrets/dash_xpub.json` (gitignored). Never put the mnemonic on the server.

## Secrets

Git ignores `.env`, `secrets/*`, `*.xprv`, `*.xpub`. CI fails if those filenames or live `DASH_XPUB=xpub…` assignments appear. `example.env` and `deploy/dashd/.env.example` are placeholders only.

A pruned Dash node for yoga lives in [`deploy/dashd/`](deploy/dashd/) — copy that folder, use `.env.example`, do not commit `.env`.

## License

MIT.

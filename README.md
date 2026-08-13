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

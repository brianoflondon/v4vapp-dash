# v4vapp-dash

Private Dash invoice bridge for [V4V.app](https://v4v.app). Own GitHub repo, own Docker image. The only caller is `v4vapp-backend-v2` on an internal Docker / Tailscale network. This service does **not** publish a public port.

Design: [`docs/design-dash-integration-bridge.md`](docs/design-dash-integration-bridge.md).

v1 (in progress): watch-only receive invoices. Outgoing payouts are designed but return 501 until a later series.

## Stack

Python 3.12, FastAPI, `bip_utils` for BIP44 (coin type 5 / testnet 1), later `dashd` JSON-RPC. Not the Dash Platform JS SDK.

## Local

```bash
cp example.env .env          # secrets only — never commit .env
uv sync --dev
uv run pytest tests/unit
# env-only (DASH_CONFIG unset). Reload is CLI uvicorn only.
uv run uvicorn v4vapp_dash.main:app --reload --port 8088
```

`python -m v4vapp_dash` has no `--reload`. Optional YAML after creating the local file:

```bash
cp tests/data/config/sample.config.yaml config/sample.config.yaml
DASH_CONFIG=config/sample.config.yaml uv run uvicorn v4vapp_dash.main:app --reload --port 8088
# or without reload:
uv run python -m v4vapp_dash --config config/sample.config.yaml
```

`GET /health` is open. `/metrics` and `/v1/*` need `X-API-Key`.

Invoices (needs Mongo + `DASH_XPUB` + `DASH_MASTER_FINGERPRINT`; quotes hit CoinGecko):

```bash
curl -s -H "X-API-Key: $DASH_API_KEY" -H "Content-Type: application/json" \
  -d '{"external_id":"dev:1","sats":25000,"expires_in_s":900}' \
  http://127.0.0.1:8088/v1/invoices
```

`POST /v1/payouts` returns 501. A background watcher polls dashd every 10s and applies InstantSend / ChainLock (or `conf_n` on regtest).

Hive config from `GET https://api.v4v.app/v1` also sets invoice min/max (`minimum_invoice_payment_sats` / `maximum_invoice_payment_sats`, currently 1–180,000) and fees: `conv_fee_percent` × sats + `conv_fee_sats` (50) + a fixed **300** sat routing pad (`DASH_ROUTING_FEE_SATS`). The Dash URI is quoted on that gross `sats_collect`. Credit on settle is still `sats_requested`.

If `cust_id` is set, create checks Hive `lightning_rate_limits` against that customer's **paid** (`SETTLED`/`OVERPAID`) sats plus the new request. Over the window → **422** `rate_limit_exceeded`.

```bash
curl -s localhost:8088/health
curl -s -H "X-API-Key: change-me-long-random" localhost:8088/metrics
```

## Docker

No host ports. The image starts `python -m v4vapp_dash` (env-only; Uvicorn access logs silenced). Compose bind-mounts `./logs:/app/logs` so `logs/v4vapp_dash.jsonl` survives restarts.

```bash
docker compose up -d --build
```

Local port 8088:

```bash
docker compose -f docker-compose.yaml -f docker-compose.regtest.yaml up --build
```

YAML is optional. After `cp tests/data/config/sample.config.yaml config/sample.config.yaml`, uncomment the sample mount and `DASH_CONFIG` in `docker-compose.yaml`. Do not set `DASH_CONFIG` without that mount.

## Config

Structure may live in a local YAML (`--config` / `DASH_CONFIG`). Secrets stay in `.env`, Compose `environment:`, or a secret file. Existing `DASH_*` and `MONGO_*` env names are unchanged and always win over YAML.

`DASH_CONFIG` (alias `V4VAPP_DASH_CONFIG`) is a three-way switch:

| Value | Behavior |
|---|---|
| unset | Env-only. Default boot, tests, CLI uvicorn. |
| empty / `none` / `-` | Env-only. Tests set `none`. |
| any other string | Load that file (`config/` prefix if not absolute). Missing file is a boot error. |

Do not put `api_key`, `rpc_password`, `xpub`, `mnemonic`, or `mongo.uri` in YAML — use `*_env_var` / `*_file`. `config/sample.config.yaml` and `config/dev.config.yaml` are gitignored. The schema contract in git is `tests/data/config/sample.config.yaml`.

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

Git ignores `.env`, `secrets/*`, `*.xprv`, `*.xpub`, and local operator YAML (`config/sample.config.yaml`, `config/dev.config.yaml`). CI fails if those filenames, live `DASH_XPUB=xpub…` assignments, or plaintext secret keys in committed YAML appear. `example.env` and `deploy/dashd/.env.example` are placeholders only.

A pruned Dash node for yoga lives in [`deploy/dashd/`](deploy/dashd/) — copy that folder, use `.env.example`, do not commit `.env`.

## License

MIT.

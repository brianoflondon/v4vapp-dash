# Dash v4vapp Integration Bridge (v4vapp-dash)

| Field | Value |
|---|---|
| **Author** | _TBD_ |
| **Date** | 2026-08-13 |
| **Status** | Draft |
| **Repo** | `v4vapp-dash` (greenfield, sibling of `v4vapp-backend-v2`) |
| **Workspace** | `/Users/bol/Documents/dev/v4vapp/v4vapp-dash` |

---

## Overview

V4V.app today accepts Lightning invoices (via LND) and MAGI/VSC on-chain events, converts requested amounts into satoshis, and settles them against Hive / KeepSats. There is no first-class Dash (DASH) payment rail. This document specifies **v4vapp-dash**: a self-contained Docker service that derives Dash receive addresses from an account **xpub** (mnemonic/xprv stay off-process in v1), issues a fresh unique address per request, watches that address for payment, and records the invoice in the **same MongoDB database** already used by `v4vapp-backend-v2`.

**v1 scope:** a private receive+watch API whose **only** caller is `v4vapp-backend-v2`, on the internal Docker network, **no public port**. The backend sends a unique identifier, a time limit, and an amount in **sats**. v4vapp-dash converts sats → Dash duffs at a quoted rate, returns a Dash P2PKH address, and transitions the invoice through a state machine that mirrors `InvoiceState` (`OPEN` / `SETTLED` / `CANCELED`) with on-chain extras (`DETECTED`, `UNDERPAID`, `OVERPAID`). **First-seen-before-expiry freezes the invoice**: a tx broadcast on time is never treated as late just because InstantSend/ChainLock arrived after `expires_at`. Incoming payments are in scope for v1. Outgoing payouts are designed but not implemented until a later PR series. The v1 API process is **watch-only** (xpub + dashd descriptor wallet); it is not a hot signer.

**Intended end-state (not v1 of this repo):** a public API for Dash wallet developers. They photograph a **Lightning invoice**, send it to the public API, and receive a **Dash payment destination**. If that Dash address is paid, the **backend** pays the Lightning invoice. The public API / frontend talks **only** to the backend (as today). The public surface must **never** call v4vapp-dash. That Lightning-invoice → Dash-address hop is a later `v4vapp-backend-v2` / `v4vapp-api-ext` feature, not a v4vapp-dash endpoint.

**Recommended stack (locked):** Python 3.12 FastAPI + official `dashd` JSON-RPC + `bip_utils` for BIP32/44 HD derivation. No Node sidecar in v1. Justification is in [Language & Runtime Decision](#language--runtime-decision) and [Key Decisions](#key-decisions).

---

## Background & Motivation

### Current V4V payment rails

The backend already has two inbound payment models that this design must align with, not replace.

1. **Lightning invoices** — created by `v4vapp-api-ext` (`POST /v1/new_invoice`, `POST /v1/new_invoice_hive`) which calls LND REST `v1/invoices` with `{value, memo, expiry}`. The backend's `lnd_monitor_v2.py` **subscribes** to LND (`SubscribeInvoices`) rather than polling, persists to Mongo collection `invoices`, and `process_invoice.py` credits KeepSats on `InvoiceState.SETTLED`. Callers poll status via `GET /v1/check_invoice/{payment_hash}`.
2. **MAGI / VSC on-chain** — `magi_monitor.py` streams indexer events into collection `magi_btc` (`MagiBTCTransferEvent` in `src/v4vapp_backend_v2/magi/magi_classes.py`). Amounts are integer sats; identity is `cust_id`.

Dash is a third rail: UTXO L1, not Lightning and not MAGI. The closest analogues are Lightning's invoice state machine + MAGI's "watch an address / indexer event and persist to a dedicated collection."

### Product story (end-state vs v1)

```mermaid
flowchart LR
  subgraph later [Later: public Dash-wallet flow]
    Dev["Dash wallet developer"]
    Pub["Public API / frontend<br/>v4vapp-api-ext"]
  end
  subgraph now [v1 and always]
    BE["v4vapp-backend-v2"]
    DASH["v4vapp-dash<br/>private Docker network"]
    LN["LND"]
  end
  Dev -->|"photo of Lightning invoice"| Pub
  Pub -->|"only talks to backend"| BE
  BE -->|"POST /v1/invoices"| DASH
  DASH -->|"SETTLED / OVERPAID"| BE
  BE -->|"pay Lightning invoice"| LN
```

- **v1:** backend-v2 is the only client. Same compose stack. No host/public publish of the dash API.
- **Later:** public developers hit api-ext / backend only. Backend creates the Dash invoice here, watches status, and on `SETTLED`/`OVERPAID` pays the photographed Lightning invoice. This repo never grows a public route for that hop.

### Why a separate repo

`v4vapp-backend-v2` is a large FastAPI + gRPC + Hive + LND monorepo. Dash keys, a `dashd` process, and a poll loop must **not** become another submodule of that tree:

- Receive-wallet (and later signer) blast radius stays in one small image.
- Dash Core's sync/prune/regtest story is independent of LND/Hive monitors.
- The shared contract is **Mongo documents + HTTP**, matching how `v4vapp-api-ext` already talks to LND without importing backend packages.

### Pain this solves

- No way today to accept DASH and credit a V4V customer in sats.
- Putting Dash keys in `v4vapp-backend-v2` YAML next to Hive active keys and LND macaroons would expand an already-sensitive config surface (`config/devdocker.config.yaml` already holds Hive WIF keys and exchange API secrets).
- JS-only Dash tooling would force a second runtime into a Python shop unless it is clearly necessary. Research (below) shows it is not necessary for L1 receive/watch.

---

## Goals & Non-Goals

### Goals (v1)

- Self-contained GitHub repo with Dockerfile, compose, CI, `example.env`, README. Default compose stands up **without** the sibling backend network.
- Hold a BIP32 **account xpub** in env/Mongo (never git). BIP39 mnemonic / xprv are **not** loaded in v1; they are required only when `DASH_PAYOUTS_ENABLED=true`.
- Generate a **fresh unique** Dash P2PKH address per request:
  - mainnet: `Bip44Coins.DASH` → `m/44'/5'/0'/0/n` (`X…`)
  - testnet **and** regtest: `Bip44Coins.DASH_TESTNET` → `m/44'/1'/0'/0/n` (`y…`)
- Private REST API: unique id + time limit + sats in → address + quoted duffs out.
- Convert sats → duffs using a stored quote snapshot (CoinGecko primary, CoinMarketCap fallback). All DASH amounts from dashd are parsed with `Decimal`, never `float * 1e8`.
- Watch for payment by **polling** only in v1. Expose a status GET **backend-v2** will poll, matching `GET /v1/check_invoice/{payment_hash}`.
- Persist invoices in the **same Mongo database** the backend already uses (`v4vapp-dev` in docker-dev, `v4vapp-backend` in production), as a **scoped user** with CRUD only on `dash_*` collections. This repo does **not** write ledger / KeepSats.
- Run production `dashd` as a **local pruned container** next to this service (`dashpay/dashd`, `prune=550`, watch-only descriptors), independent of Umbrel/LND.
- Plan outgoing payouts (UTXO selection, change, fees) without implementing them.
- Join the backend Docker network internally; do not become a backend submodule and do not import `v4vapp_backend_v2`. **No public port** on the dash API.

### Non-goals (v1)

- Public internet exposure of the API, any published host port in production, or any key material.
- A public “Lightning invoice in → Dash address out” endpoint (that hop lives in backend / api-ext later; they call this private API).
- Direct connections from `v4vapp-api-ext`, the frontend, or third-party Dash wallets to this module.
- Dash Platform (L2 identities, documents, DashJS `dash` npm package).
- CoinJoin / PrivateSend.
- Automatic credit into KeepSats / Hive ledger (PR 12 + a separate `LedgerType` design in **backend-v2**).
- Implementing `POST /v1/payouts`.
- Running Insight / Blockbook as a required local service.
- Using Umbrel/Tailscale dashd as the production node (local pruned container is the decision).
- Address reuse, BIP47 payment codes, or silent payments.
- Multi-tenant wallets (one HD account for the service).

---

## Existing System (cited)

This section is the integration surface. v4vapp-dash must speak this dialect.

### Mongo connection and replica sets

`DBConn` (`/Users/bol/Documents/dev/v4vapp/v4vapp-backend-v2/src/v4vapp_backend_v2/database/db_pymongo.py`) builds:

```
mongodb://{user}:{password}@{hosts}/{authSource=db_name}{&replicaSet=...}
```

Client flags used in production: `tz_aware=True`, `retryWrites=True`, `retryReads=True`, `readPreference=primaryPreferred`, `w=1`, `journal=True`, `appName=...`.

| Env | Connection name | Hosts | Replica set | DB name | User |
|---|---|---|---|---|---|
| Docker-dev | `local_connection` | `dot.tail400e5.ts.net:37017` | `rsPytest` | `v4vapp-dev` | `v4vapp-dev-user` |
| Compose local mongo | `mongo-pytest-local` | container port **37017** (must match host) | `rsPytest` | same | same |
| Production | `gad-edi-dave` | `gad-v4vapp`, `edi-v4vapp`, `dave-v4vapp` `.tail400e5.ts.net:27017` | `rsV` | `v4vapp-backend` | `v4vapp-user` |

Users and indexes are declared in YAML (`DbsConfig` / `CollectionConfig` / `IndexConfig` in `config/setup.py`) and created at startup by `DBConn.setup_collections_indexes`. Collection names in the live dev DB:

- `invoices` — unique `r_hash`, unique `add_index`
- `payments` — unique `payment_hash`, unique `payment_index`
- `htlc_events`, `pub_keys`
- `hive_ops` — compound unique `(block_num, trx_id, op_in_trx, realm)`
- `ledger`, `ledger_checkpoints`
- `pending_rebalances` in code and `devdocker.config.yaml`; production YAML key is `pending-rebalances` (hyphen) — a backend naming inconsistency, not something dash should copy
- `magi_btc` — unique `group_id`, unique `indexer_tx_hash`
- timeseries: `rates_ts` (`timeField: timestamp`, granularity minutes), `lnd_balances_ts`

**v4vapp-dash will add `dash_invoices`, `dash_wallet_state`, and later `dash_payouts` to this same database.** It creates its own indexes on startup (idempotent). It must **not** use `v4vapp-dev-user` / `v4vapp-user` (`readWrite` + `dbAdmin` on the whole DB). Use a dedicated `v4vapp-dash-user` with CRUD only on the three `dash_*` collections (see [Scoped Mongo user](#scoped-mongo-user)). A companion YAML snippet for backend is required for production, not for dash to create indexes.

`DBConn` **strips auth** when `replica_set == "rsPytest"`. Compose `mongo-pytest-local` is started **without `--auth`**. Dev URI against that replica set must be unauthenticated. Production `rsV` uses user/password.

### Lightning invoice analogue

`InvoiceState` in `models/invoice_models.py`:

```python
class InvoiceState(StrEnum):
    OPEN = "OPEN"
    SETTLED = "SETTLED"
    CANCELED = "CANCELED"
    ACCEPTED = "ACCEPTED"
```

Creation (api-ext `invoices_routers.py` + `lightning_node.py`):

```python
data = {"value": new_inv.amount, "memo": memo, "expiry": new_inv.expiry, "private": True}
# POST {LIGHTNING_NODE_SERVER_URL}v1/invoices
# amount is sats (int). LNURL path uses msats.
# MAX_INVOICE_TIME = 1800 seconds
```

Caller identity is encoded in the memo (`hive_accname | message #HBD|#SATS|#v4vapp`), parsed later by `Invoice.fill_cust_id()` via `LND_INVOICE_TAG`. Settlement watching is a **gRPC subscription** in `lnd_monitor_v2.py`; the **public/status** interface the rest of the system uses is still a **poll**: `GET /v1/check_invoice/{payment_hash}` returning `InvoiceStatus {settled, paid, state, expired}`.

Expired unpaid Lightning invoices are pruned by `delete_expired_unsettled_invoices` after `retain_after_expiry` (default 1 day). Dash invoices that received funds must **never** be TTL-deleted.

### Sats representation

- User-facing amounts: **integer sats**.
- Internal Lightning: **integer msats**, stored as `BSONInt64` (`models/pydantic_helpers.py`, wraps `bson.Int64`).
- `SATS_PER_BTC = 100_000_000` in `helpers/crypto_prices.py`.
- Quotes live in timeseries `rates_ts` (`DB_RATES_COLLECTION`) and Redis cache (`CACHE_TIMES`: CoinGecko 360s, Binance 120s, CMC 720s).
- `Currency` enum today: `HIVE, HBD, USD, SATS, MSATS, BTC, MAGISATS`. **No DASH.** v4vapp-dash keeps DASH conversion inside its own quote module and stores a snapshot on the invoice. It does not write `rates_ts` (schema is Hive/BTC-specific: `hive_usd, hbd_usd, btc_usd, ...`).
- `CryptoConversion` converts among those currencies using `QuoteResponse`. Dash conversion is a separate two-hop: sats → USD (via `btc_usd`) → DASH (via `dash_usd`), or direct `dash` vs `btc` from CoinGecko.

### Auth patterns

- `api_v2.py` is **unauthenticated** on the Docker/Tailscale network. Public surface is `v4vapp-api-ext` behind Traefik on `traefik-public`, with optional basic-auth middleware and rate limits. LND is authenticated with a macaroon header (`Grpc-Metadata-macaroon`).
- Exchange credentials use env-var indirection (`api_key_env_var`) in newer config; Hive keys and some API keys are still plaintext in YAML. **v4vapp-dash must not copy that YAML-secret pattern for xpub, mnemonic, or API keys.**
- There is no existing internal API-key middleware to reuse. We introduce one.

### Docker / network

`v4vapp-backend-v2/docker-compose.yaml` declares network `v4vapp-backend` as a project-local bridge (`driver: bridge`, **no** `name:`, **no** `external: true`). Compose therefore publishes it as `{project}_v4vapp-backend`, typically `v4vapp-backend-v2_v4vapp-backend`. Confirm with `docker network ls`. Services on that network: `hive-monitor`, `db-monitor`, `magi-monitor`, `api-v2`, `admin-interface`, `mongo-pytest-local`, `redis-pytest-local`. **`umbrel-node-monitor` is not attached** (no `networks:` key).

Mongo **must** use port 37017 inside and outside the replica-set member so `rsPytest` hostnames resolve. `admin-interface` publishes **`0.0.0.0:8080:8080`** — dash must **not** bind host 8080. Public HTTPS is a **separate** compose (`v4vapp-ext-traefik`, `traefik-ssl-cloudflare`) on external network `traefik-public`. Private services stay on Tailscale (`*.tail400e5.ts.net`).

Default dash compose uses its **own** internal network so `docker compose up` works on a greenfield clone. Production overlay joins the *actual* backend network name. From `api-v2` the service is `http://v4vapp-dash:8080` (container port only). **Production publishes no host port.** A local/regtest overlay may bind `127.0.0.1:8088:8080` for operator debugging; never `0.0.0.0` and never `traefik-public`.

---

## Research: Dash libraries and watch options

Researched 2026-08-13. Names below are real packages, not guesses.

### 1. `@dashevo/dashcore-lib` (official L1 JS)

- Repo: [dashpay/dashcore-lib](https://github.com/dashpay/dashcore-lib), npm `@dashevo/dashcore-lib`.
- Dash Core Group's maintained fork of bitcore-lib. Documents addresses, HD keys, mnemonics (via `@dashevo/dashcore-mnemonic`), transactions, units, InstantSend-aware unspent outputs.
- Companion packages: `@dashevo/dashd-rpc` (JSON-RPC client), `@dashevo/dashcore-mnemonic`, `@dashevo/dashcore-p2p`.
- **This is the most solid in-process JS library for L1 address/tx construction.**
- `bitcore-lib-dash` on npm (0.15.3, last publish 2018) is stale and must not be used.

### 2. `dash` npm / DashJS / `@dashevo/wallet-lib`

- Official Dash docs: DashJS "allows you to transact on L1 **or** fetch/register documents on L2."
- The `dash` package is the Platform SDK (DAPI, identities, documents, Drive). Wallet features go through `@dashevo/wallet-lib` talking to **DAPI**, not a local `dashd`.
- **Wrong primary stack** for a custodial invoice service that must control UTXOs, InstantSend policy, and a hot wallet. Do not take this dependency in v1.

### 3. `dashd` JSON-RPC (official node)

Documented at [docs.dash.org core RPC](https://docs.dash.org/en/stable/docs/core/api/remote-procedure-calls-wallet.html). Relevant methods:

| Method | Role |
|---|---|
| `createwallet` | Create a **descriptor, disable_private_keys, blank** wallet before any import. Required. Dash Core ≥ 21. |
| `getnewaddress` | Node-owned HD address. Avoid as the primary generator so keys are not locked inside `wallet.dat`. |
| `getdescriptorinfo` | Returns the BIP380 checksum. `importdescriptors` **requires** `desc#checksum`. |
| `importdescriptors` | Watch-only ranged `pkh(xpub/0/*)` and `pkh(xpub/1/*)`. Use `timestamp: "now"` on a pruned node. |
| `getreceivedbyaddress` | Returns **DASH as a JSON number** (float). Convert with `Decimal`, never `float * 1e8`. `minconf`; InstantSend-locked option (`addlocked`). |
| `listunspent` | Same float-amount trap. UTXO set for an address list; used for settlement proof and later payouts. |
| `gettransaction` / `getrawtransaction` | Tx details, InstantSend lock, ChainLock. |
| `estimatesmartfee` | Fee rate for outgoing. Dash Core returns **DASH per kvB** (1000 vbytes), same family as Bitcoin Core ≥ 0.15. Convert to duffs/kvB with `Decimal`. |
| `lockunspent` | Reserve UTXOs between `BUILDING` and `BROADCAST` (payouts, later). |
| `createrawtransaction` / `fundrawtransaction` / `signrawtransactionwithkey` / `sendrawtransaction` | Outgoing without importing private keys into dashd. Since Dash 0.14, qualifying txs are **automatically InstantSend**; do not pass a leftover `use_is` flag as if it were required. |
| `sendtoaddress` | Only if we later choose a dashd-held hot wallet. `use_is` is leftover UX. |
| ZMQ `hashtx`, `hashblock`, `rawtx` | Push notifications. **Not in v1.** Optional later PR. |

Official image: [`dashpay/dashd`](https://hub.docker.com/r/dashpay/dashd) (entrypoint `dash_oneshot`, data volume `/home/dash`, `TESTNET=1` supported). Pin **≥ 21.0** for descriptor wallets. Default RPC ports: mainnet **9998**, testnet **19998**, regtest **19898**. Compose **always** sets `-rpcport=9998` so the node root `http://dashd:9998` is network-independent. Wallet RPCs always use `/wallet/watch` (see [dashd watch-only bootstrap](#dashd-watch-only-bootstrap)). Regtest is a first-class Dash Core mode (`-regtest`), documented in [Testing Applications](https://docs.dash.org/en/stable/docs/core/examples/testing-applications.html).

### 4. Indexers and hosted APIs

| Option | What it is | Verdict |
|---|---|---|
| **Dash Insight** (`dashpay/insight-api`) | Official REST + websocket on top of dashcore-node. Endpoints like `/insight-api/addr/:addr`. Public UI at explorer.dash.org. | Good public fallback. Running our own Insight is another full node + indexer — too heavy for v1. |
| **Blockbook** | Treacle/NowNodes/GetBlock expose `/api/v2/address/:addr` with UTXOs, txs, InstantSend metadata. | Best hosted poll target if dashd is unavailable (CI, early boot). |
| **NowNodes / GetBlock** | Paid Dash RPC + Blockbook. | Configured as optional `WATCH_FALLBACK_URL`. Not the source of truth when local dashd is synced. |
| **Dash Electrum** | Desktop wallet with its own RPC (`listaddresses`, `listunspent`). | Not a service we run. |
| **Bitquery / BlockCypher** | Third-party analytics / payment APIs. | Unnecessary vendor lock-in. |

### 5. Units, HD, InstantSend, ChainLocks

- **1 DASH = 100,000,000 duffs** (identical scale to sats). Always **store** integer duffs. dashd RPC amounts arrive as JSON floats (DASH); parse with `Decimal(str(value))` and convert via `int((dash * Decimal("100000000")).to_integral_value(rounding=ROUND_DOWN))`. Never `float * 1e8`.
- **SLIP-44:** mainnet coin type **5** → `m/44'/5'/account'/change/index`. Testnet **and** regtest use SLIP-44 type **1** (`Slip44.TESTNET`). `bip_utils` `Bip44Conf.DashTestNet` sets `coin_idx=1`. Dash Core `getaddressinfo` examples use `m/44'/1'/0'/0/1`. Receive: `change=0`. Change: `change=1`.
  - `Bip44Coins.DASH` → `m/44'/5'/0'/…`, address version `X…`
  - `Bip44Coins.DASH_TESTNET` → `m/44'/1'/0'/…`, address version `y…` (regtest uses the same testnet version)
- **InstantSend**: masternode quorum locks inputs; `isdlock` is network-visible in seconds. Inputs qualify if they are InstantSend-locked, ChainLocked, or have ≥**6 mainnet / 2 testnet-regtest** confirmations. That same 6/2 table is the **inbound settlement fallback** when neither IS nor ChainLock is present — not `minconf=1`.
- **ChainLocks**: a quorum signs the tip; a ChainLocked block is treated as final. A 1-conf block that is **not** ChainLocked can reorg; do not treat 1-conf as final on mainnet.

### 6. Fresh unique addresses without `getnewaddress`

Recommended (and what we will implement). Full bootstrap is specified in [dashd watch-only bootstrap](#dashd-watch-only-bootstrap).

1. v1 loads **account xpub** from `DASH_XPUB` (or, one-shot operator tool: derive xpub from mnemonic, persist, **discard the seed**). The API process never keeps a mnemonic/xprv in RAM unless payouts are enabled.
2. Derive receive addresses from that xpub with `bip_utils` (`Bip44.FromExtendedKey`). Never call `getnewaddress`.
3. Persist next index in Mongo `dash_wallet_state` keyed by **network** (atomic `$inc`).
4. On first boot against a fresh dashd: `createwallet("watch", disable_private_keys=true, blank=true, descriptors=true)` → `getdescriptorinfo` for receive `…/0/*` and change `…/1/*` → `importdescriptors` with **checksum** and `timestamp: "now"` (pruned nodes cannot rescan from `0`).
5. Default import range **`[0, 100000]`**. Extend via a second `importdescriptors` with a higher `range` on the same checksummed desc **before** `next_index` hits the end. `index_exhausted` is returned only after that extend RPC fails.

### 7. Watching those addresses

| Method | Latency | Docker-friendly | v1? |
|---|---|---|---|
| Poll dashd `getreceivedbyaddress` / `listunspent` for watched addresses | 10–15 s | Yes | **v1 only** |
| Poll Blockbook/Insight fallback | 10–15 s | Yes (HTTPS) | v1 failover if dashd down |
| ZMQ `hashtx`/`hashblock` | ~1 s | Yes if dashd local | **Not in v1** (later PR) |
| Insight websockets | ~1 s | Extra service | No |

At the expected load (see [Quantification](#quantification)) a **batched** `listunspent(..., [addresses])` every 10 s is ~6 RPC/min for the batch plus a handful of `gettransaction` calls (~**20 RPC/min** typical). Not 3–5 RPC/s.

### 8. Testnet / regtest

- **Regtest** (compose profile `regtest`): `dashd -regtest -rpcport=9998`, `generatetoaddress` to mine, deterministic **xpub** in `example.env`. Full integration tests run here. No masternodes → `DASH_SETTLE_POLICY=conf_n` with `DASH_MIN_CONF=1` after we mine a block. Addresses are testnet-version `y…` from `m/44'/1'/0'/0/n`.
- **Testnet** (profile `testnet`): `dashpay/dashd` + `TESTNET=1`. Real InstantSend. Faucet for funding. Optional; not required for CI. Same BIP44 path as regtest. Fallback confirmations if IS/CL missing: **2**.
- **Mainnet**: pruned dashd (`prune=550` or larger) **plus** a watch-only descriptor wallet imported with `timestamp: "now"`. Pruning is compatible with a wallet; it is **not** compatible with `txindex=1`. We do not need `txindex` if every receive address is in the wallet descriptor. Policy: InstantSend **or** ChainLock; fallback confirmations if both missing: **6**.

A full unpruned Dash chain is tens of GB. Dev machines should not sync mainnet. Production can run pruned (~2–10 GB) or use a dedicated host's dashd over Tailscale, the same way LND is `umbrel.tail400e5.ts.net:10009`.

### Python L1 options (for completeness)

| Library | Notes |
|---|---|
| **`bip_utils`** | Actively maintained. First-class Dash + Dash testnet BIP44 P2PKH. **Chosen for derivation.** |
| **`bitcoinrpc` / raw httpx JSON-RPC** | Language-agnostic. **Chosen for dashd I/O.** |
| `bitcoinlib` (1200.co) | Advertises Dash wallets + service providers. Heavy, opinionated, pulls third-party explorers. Rejected as a core dependency. |
| `pycoin` | Listed on official Dash SDK page; last serious Dash mention is Python 3.6/3.7 era. Not chosen. |
| `python-dashrpc` | Thin RPC wrapper, low activity. We will speak JSON-RPC ourselves. |

---

## Language & Runtime Decision

**Default: Python 3.12 + FastAPI + Uvicorn, talking to `dashd` over JSON-RPC, deriving keys with `bip_utils`.**

| Option | Pros | Cons | Decision |
|---|---|---|---|
| **A. FastAPI + dashd RPC + bip_utils** | Matches `api_v2.py`, `v4vapp-api-ext`, `v4vapp-binance`. Official chain semantics (InstantSend, ChainLocks). No JS in the hot path. HD derivation is well-tested in Python. One runtime. | Must run or reach a dashd. In-process tx scripts are less "Dash-native" than dashcore-lib (mitigated: dashd signs via `signrawtransactionwithkey`). | **Chosen** |
| B. TypeScript (Fastify/Hono) + dashcore-lib | Best in-process L1 library. Official. | Second language in a Python org. Duplicates FastAPI/Pydantic/Mongo habits. DashJS temptation toward Platform. | Rejected for v1 |
| C. FastAPI + Node sidecar for signing | Uses dashcore-lib only where it is strongest. | Two images, two health checks, two failure domains, for tens of invoices/day. | Deferred. Revisit only if InstantSend raw-tx construction in Python proves insufficient. |

The user preferred FastAPI and was open to TS *because Dash libs are JS*. The evidence is that the **necessary** JS lib (`dashcore-lib`) is an L1 primitive library, while the **operational** system of record is `dashd`. Python can derive P2PKH addresses from an xpub (`bip_utils`: coin type 5 mainnet, type 1 testnet/regtest). v1 never signs. Planned outgoing uses `signrawtransactionwithkey` with WIFs derived only when payouts are enabled.

---

## Proposed Design

### High-level architecture

```mermaid
flowchart LR
  subgraph callers [Only caller: internal Docker network]
    API2["v4vapp-backend-v2 api-v2"]
  end

  subgraph dashbox [v4vapp-dash compose]
    API["FastAPI :8080 in-container<br/>no public port / X-API-Key"]
    WATCH["Watcher loop<br/>poll 10s"]
    HD["xpub derivation<br/>bip_utils"]
    Q["QuoteService<br/>CoinGecko / CMC"]
    DASHD["dashd ≥21<br/>watch-only descriptor wallet"]
  end

  subgraph shared [Existing shared infra]
    MONGO[("Mongo replica<br/>v4vapp-dev / v4vapp-backend")]
    CG["CoinGecko / CMC"]
    BB["Blockbook fallback<br/>optional"]
  end

  API2 -->|"POST /v1/invoices<br/>GET /v1/invoices/id"| API
  API --> HD
  API --> Q
  API --> MONGO
  Q --> CG
  HD -->|"address only"| API
  API -->|"createwallet + importdescriptors"| DASHD
  WATCH --> DASHD
  WATCH --> MONGO
  WATCH -.-> BB
```

The API process and the watcher run in **one container** (FastAPI lifespan starts the poll task), matching how a small sibling service like `v4vapp-binance` stays a single process. `dashd` is a second container in the same compose file.

### Sequence: create → watch → settle / expire

```mermaid
sequenceDiagram
  autonumber
  participant B as v4vapp-backend / caller
  participant A as v4vapp-dash API
  participant Q as QuoteService
  participant W as HDWallet
  participant M as Mongo dash_invoices
  participant D as dashd watch-only
  participant P as Payer

  B->>A: POST /v1/invoices {external_id, expires_in_s, sats}
  A->>A: auth X-API-Key
  A->>M: unique check external_id
  A->>Q: quote sats → duffs
  Q-->>A: dash_usd, btc_usd, duffs, quote_id
  A->>W: next receive address (atomic $inc)
  W-->>A: addr, path (mainnet m/44'/5'/… or testnet m/44'/1'/…)
  A->>M: insert OPEN {expires_at, settle_deadline_at}
  A-->>B: 201 {invoice_id, address, duffs, expires_at, settle_deadline_at, quote}

  loop every 10s while OPEN or DETECTED
    A->>D: listunspent / gettransaction
    D-->>A: amounts (DASH floats → Decimal duffs), instantlock, chainlock
  end

  P->>D: pay address (InstantSend if wallet supports it)
  Note over A,M: first seen before expires_at → DETECTED (frozen)<br/>policy met → SETTLED / OVERPAID<br/>new output after expires_at → late_payment, not credited
  A->>M: state update

  B->>A: GET /v1/invoices/{id}
  A->>M: read
  A-->>B: {state, duffs_received, txids, settled_at}
```

Two clocks, not one:

| Clock | Meaning |
|---|---|
| `expires_at` | Stop accepting **new** first-seen outputs toward this invoice. |
| `settle_deadline_at` | Keep waiting for InstantSend / ChainLock / `conf_n` on outputs **already first-seen** before `expires_at`. Default `expires_at + DASH_SETTLE_GRACE_S` (3600 s). |

- **EXPIRED** only if `now >= expires_at` **and** no output was first-seen before `expires_at`.
- **UNDERPAID** only if pre-expiry outputs exist, their sum is below tolerance, and `now >= expires_at` (no more new outputs count).
- A non-IS tx first seen at T+14m on a 15m invoice stays `DETECTED` until IS, ChainLock, or the `conf_n` fallback. Confirmation at T+16m is **not** late.
- **Late** = first seen **after** `expires_at` (or after `CANCELED`). Flag `late_payment=true`, do not auto-credit, do not delete, sweep later.
- **Cancel + funds:** `POST .../cancel` is allowed only in `OPEN` with zero first-seen outputs. If a payment arrives after cancel, treat it as late (same as expired+pay). Cancel is rejected (`409 invoice_not_cancelable`) once `DETECTED`.

### Repo layout

```
v4vapp-dash/
├── README.md
├── LICENSE
├── pyproject.toml                 # uv, python >=3.12
├── uv.lock
├── example.env
├── Dockerfile                     # multi-stage, uv, python:3.12-slim (same pattern as backend)
├── docker-compose.yaml            # api + dashd + optional join to v4vapp-backend
├── docker-compose.regtest.yaml    # override: dashd -regtest
├── .dockerignore
├── .gitignore                     # .env, secrets/, *.xprv, mnemonics
├── .github/workflows/ci.yml       # lint, unit, regtest integration
├── config/
│   └── sample_config.yaml         # non-secret defaults
├── docker-compose.backend-net.yaml # optional: join real backend network name
├── secrets/                       # gitignored; xpub file or (payouts) mnemonic
│   └── .gitkeep
└── src/v4vapp_dash/
    ├── __init__.py
    ├── main.py                    # FastAPI app + lifespan
    ├── config.py                  # pydantic-settings from env
    ├── api/
    │   ├── deps.py                # X-API-Key
    │   ├── health.py
    │   └── v1/
    │       ├── invoices.py
    │       └── payouts.py         # stub 501 in v1
    ├── models/
    │   ├── invoice.py             # DashInvoice, DashInvoiceState
    │   ├── quote.py
    │   └── wallet.py
    ├── db/
    │   ├── mongo.py               # AsyncMongoClient, indexes
    │   └── indexes.py
    ├── wallet/
    │   ├── hd.py                  # bip_utils xpub derivation
    │   └── descriptors.py         # getdescriptorinfo + importdescriptors
    ├── dashd/
    │   ├── rpc.py                 # JSON-RPC client
    │   ├── bootstrap.py           # createwallet + checksummed import
    │   └── watch.py               # listunspent / gettransaction + Decimal
    ├── quotes/
    │   └── service.py             # CoinGecko + CMC
    ├── watcher/
    │   └── loop.py                # poll OPEN invoices
    └── logging.py
└── tests/
    ├── unit/
    ├── integration/               # needs dashd regtest
    └── fixtures/
```

This mirrors `v4vapp-api-ext` (`src/v4vapp_api_ext/...`) and `v4vapp-binance` (`src/v4vapp_binance/...`) rather than the backend's deeper package tree.

### Process model

- **One FastAPI worker** (`uvicorn`, workers=1). The watcher holds in-process state (in-flight invoice ids). Multiple workers would double-poll and race `$inc` on the address index (Mongo `$inc` is atomic, but we still want a single watcher).
- Lifespan: connect Mongo → ensure indexes → load **xpub** (refuse boot if `DASH_NETWORK` ≠ stored wallet-state network/fingerprint) → `createwallet` if missing → ensure checksummed descriptors imported with `timestamp: "now"` if first boot → start watcher task → yield → cancel watcher → close clients.
- Health: `/health` does not require the API key. It reports Mongo ping, dashd `getblockchaininfo` (blocks, chain, pruned), wallet next index, descriptor range, watcher heartbeat age.
- `/metrics` **requires** `X-API-Key` (same as `/v1/*`). `/health` stays open for compose healthchecks.

---

## API / Interface Changes

Private API, prefix `/v1`. OpenAPI served at `/docs` **only when `DASH_DOCS_ENABLED=true`** (dev). Production disables docs.

### Auth

Every `/v1/*` route requires header:

```
X-API-Key: <shared secret>
```

Compared with `hmac.compare_digest` against `DASH_API_KEY`, then `DASH_API_KEY_PREV` if set (rotation). Missing/wrong key → `401` with `WWW-Authenticate: ApiKey`. No cookies, no JWT, no mTLS in v1 (Tailscale + Docker network is the transport isolation; the key is defense in depth). Optional second factor: bind `DASH_BIND=127.0.0.1` and reach the container only via Tailscale / compose DNS.

`/health` and `/` are open (same as `api_v2.py` `/health`). `/metrics` and every `/v1/*` route require the key.

### `POST /v1/invoices`

Create a Dash invoice.

**Request**

```json
{
  "external_id": "hive:v4vapp-test:2026-08-13T12:00:00Z:a1b2",
  "sats": 25000,
  "expires_in_s": 900,
  "cust_id": "v4vapp-test",
  "memo": "optional human note",
  "min_confirmations": null
}
```

| Field | Type | Rules |
|---|---|---|
| `external_id` | string | Required. 1–128 chars. Unique. Caller-defined idempotency key (Hive acc + timestamp + nonce, or a UUID). |
| `sats` | int | Required. `1 ≤ sats ≤ 50_000_000` (0.5 BTC). Integer sats, same as `NewInvoiceBasic.amount`. |
| `expires_in_s` | int | Required. `60 ≤ expires_in_s ≤ 86400`. Default recommendation 900. Lightning analogue max is 1800; Dash on-chain needs a longer ceiling because mempool + InstantSend can still take minutes if the sender does not use InstantSend. |
| `cust_id` | string \| null | Optional. Stored for the backend; not interpreted. Analogous to `Invoice.cust_id`. |
| `memo` | string \| null | Optional. Max 300 chars (api-ext invoice memo limit). |
| `min_confirmations` | int \| null | Only used when `DASH_SETTLE_POLICY=conf_n`. Ignored under `instantsend_or_chainlock`. `null` = service default for that policy. |

**Response `201`**

```json
{
  "invoice_id": "66b2c0e1f4a0b91c2d3e4f50",
  "external_id": "hive:v4vapp-test:2026-08-13T12:00:00Z:a1b2",
  "state": "OPEN",
  "address": "XdashMainnetAddressHere11111111111",
  "uri": "dash:XdashMainnetAddressHere11111111111?amount=0.50000000",
  "network": "mainnet",
  "sats_requested": 25000,
  "duffs_quoted": 50000000,
  "dash_quoted": "0.50000000",
  "expires_at": "2026-08-13T12:15:00Z",
  "settle_deadline_at": "2026-08-13T13:15:00Z",
  "created_at": "2026-08-13T12:00:00Z",
  "quote": {
    "source": "coingecko",
    "fetched_at": "2026-08-13T12:00:00Z",
    "btc_usd": "65000.00",
    "dash_usd": "32.50",
    "dash_btc": "0.00050000",
    "sats_per_dash": 50000,
    "ttl_s": 60
  },
  "derivation": {
    "account": 0,
    "change": 0,
    "index": 42,
    "path": "m/44'/5'/0'/0/42"
  },
  "policy": {
    "settle_policy": "instantsend_or_chainlock",
    "underpay_tolerance_duffs": 50000,
    "underpay_tolerance_bps": 100,
    "min_confirmations": 6,
    "accept_instantsend": true,
    "accept_chainlock": true
  }
}
```

`derivation` is returned so operators can recover the key from an offline backup of the mnemonic. It is **not** a secret. v1 does not have the private key in-process. Production logs treat `path` as operational metadata.

**Errors**

| HTTP | `error.code` | When |
|---|---|---|
| 400 | `invalid_request` | Schema / range |
| 401 | `unauthorized` | Bad API key |
| 409 | `duplicate_external_id` | Same `external_id` already exists (different payload) |
| 409 | `invoice_not_cancelable` | Cancel requested but not `OPEN` or funds already first-seen |
| 422 | `quote_unavailable` | All price sources failed |
| 503 | `wallet_unavailable` | dashd down or descriptor wallet missing |
| 503 | `index_exhausted` | `next_index` beyond range **and** automatic `importdescriptors` extend failed |

Idempotency: a second POST with the same `external_id` **and identical sats/expiry** returns `200` with the existing invoice (safe retry). Different payload → `409`.

### `GET /v1/invoices/{invoice_id}`

`invoice_id` is the Mongo `_id` hex, **or** the caller may use `GET /v1/invoices/by-external/{external_id}`.

**Response `200`** — create payload plus:

```json
{
  "state": "SETTLED",
  "duffs_quoted": 50000000,
  "duffs_received": 50000000,
  "sats_credited": 25000,
  "expires_at": "2026-08-13T12:15:00Z",
  "settle_deadline_at": "2026-08-13T13:15:00Z",
  "first_seen_at": "2026-08-13T12:03:11Z",
  "txids": [
    {
      "txid": "abc…",
      "vout": 0,
      "duffs": 50000000,
      "confirmations": 1,
      "instantlock": true,
      "chainlock": true,
      "detected_at": "2026-08-13T12:03:11Z"
    }
  ],
  "settled_at": "2026-08-13T12:03:11Z",
  "expired": false,
  "late_payment": false
}
```

This is the endpoint the backend polls, analogous to `GET /v1/check_invoice/{payment_hash}`.

Suggested poll cadence for the backend: **2 s for the first 30 s, then 5 s, until `settle_deadline_at + 60 s`** (not merely `expires_at`). The Dash watcher is already polling the chain; this GET is a cheap Mongo read.

### `GET /v1/invoices?state=OPEN&cust_id=`

Operator listing. Paginated:

- `limit` default 50, max 200.
- Sort: `created_at ASC`, then `_id ASC`.
- `cursor` is an opaque string `"{created_at.isoformat()}|{_id}"` from the last row of the previous page.
- Next page: `created_at > cursor.ts OR (created_at == cursor.ts AND _id > cursor.id)`.
- Response includes `next_cursor` (`null` if last page).

### `POST /v1/invoices/{invoice_id}/cancel`

Only if `state == OPEN` **and** `first_seen_at is null`. Sets `CANCELED`. Address remains reserved (never reused). A payment first-seen after cancel is `late_payment=true` on the still-`CANCELED` document; it is not credited and is picked up by the unswept-UTXO watch.

### `POST /v1/payouts` (designed, v1 returns `501`)

```json
{
  "external_id": "payout:…",
  "address": "Xdestination…",
  "duffs": 10000000,
  "sats": null,
  "cust_id": "v4vapp-test",
  "subtract_fee": false
}
```

Either `duffs` or `sats` (converted at a fresh quote). See [Outgoing payments (designed)](#outgoing-payments-designed).

### `GET /health`

```json
{
  "status": "ok",
  "version": "0.1.0",
  "network": "regtest",
  "mongo": true,
  "dashd": {"chain": "regtest", "blocks": 150, "synced": true},
  "wallet": {"network": "regtest", "next_receive_index": 7, "descriptor_range_end": 100000},
  "watcher": {"last_tick_age_s": 3.1, "open_invoices": 2}
}
```

`status` is `degraded` if dashd is syncing or fallback indexer is in use; `error` if Mongo is down.

---

## Data Model Changes

### Collection `dash_invoices`

Same database as backend (`v4vapp-dev` / `v4vapp-backend`). Amounts that must survive JSON and Mongo safely:

- `sats_*` → `bson.Int64` (same idea as `BSONInt64`)
- `duffs_*` → `bson.Int64`
- quote prices → `Decimal128` (same as `QuoteResponse` / `HiveRatesDB`)
- timestamps → timezone-aware UTC datetime

```python
class DashInvoiceState(StrEnum):
    OPEN = "OPEN"
    DETECTED = "DETECTED"       # seen on-chain, not yet meeting policy
    SETTLED = "SETTLED"         # policy met; analog of InvoiceState.SETTLED
    UNDERPAID = "UNDERPAID"     # expired or finalized below tolerance
    OVERPAID = "OVERPAID"       # policy met, received > quoted (still success)
    EXPIRED = "EXPIRED"         # analog of Lightning CANCELED-by-timeout
    CANCELED = "CANCELED"       # explicit cancel, no funds
```

`OVERPAID` is a terminal **success** state (treat like SETTLED when crediting). **Decided:** credit `sats_requested` only. Surplus Dash stays in the receive wallet as working capital / fee buffer (not credited, not auto-swept). `UNDERPAID` credits **nothing**; the invoice stays `UNDERPAID` for ops to refund or wait.

Document shape:

```python
{
  "_id": ObjectId,
  "external_id": str,              # unique
  "cust_id": str | None,
  "memo": str | None,
  "state": DashInvoiceState,
  "address": str,                  # unique
  "network": "mainnet" | "testnet" | "regtest",
  "path": str,                     # m/44'/5'/0'/0/n or m/44'/1'/0'/0/n
  "account": 0,
  "change": 0,
  "index": Int64,
  "sats_requested": Int64,
  "duffs_quoted": Int64,
  "duffs_received": Int64,         # sum of watched outputs
  "sats_credited": Int64 | None,   # set on SETTLED/OVERPAID
  "quote": { source, fetched_at, btc_usd, dash_usd, dash_btc, sats_per_dash, ttl_s },
  "policy": { settle_policy, underpay_tolerance_duffs, underpay_tolerance_bps,
              min_confirmations, accept_instantsend, accept_chainlock },
  "txids": [ { txid, vout, duffs, confirmations, instantlock, chainlock, detected_at } ],
  "created_at": datetime,
  "expires_at": datetime,            # stop accepting NEW first-seen outputs
  "settle_deadline_at": datetime,    # keep waiting on pre-expiry outputs
  "first_seen_at": datetime | None,  # first output observed (freezes expiry)
  "detected_at": datetime | None,
  "settled_at": datetime | None,
  "canceled_at": datetime | None,
  "late_payment": bool,              # first seen after expires_at or cancel
  "swept_at": datetime | None,       # set by future payout path
  "updated_at": datetime,
}
```

**Indexes** (created by v4vapp-dash on startup, same style as `CollectionConfig`):

| Name | Key | Unique |
|---|---|---|
| `external_id` | `external_id` | yes |
| `address` | `address` | yes |
| `state_expires` | `state`, `expires_at` | no |
| `cust_id` | `cust_id` | no |
| `created_at` | `created_at` | no |

No TTL index. Documents live for the life of the address (which is never reused). Lightning analogue deletes unpaid invoices after 1 day; Dash addresses can receive late money, so we **keep** every document. The watcher scans **all unswept** `OPEN|DETECTED|EXPIRED|CANCELED` addresses (see [Watching implementation](#watching-implementation)), not a 24 h window.

### Collection `dash_wallet_state` (one doc per network)

```python
{
  "_id": "mainnet",              # == DASH_NETWORK: mainnet | testnet | regtest
  "network": "mainnet",          # denormalized; must equal _id
  "fingerprint": "a1b2c3d4",     # BIP32 master fingerprint from derive_xpub (not secret)
  "account_xpub": "xpub…",       # watch-only, safe to store
  "next_receive_index": Int64,
  "next_change_index": Int64,    # reserved for outgoing
  "descriptor_range_end": Int64, # default 100000
  "updated_at": datetime,
}
```

Boot **refuses to start** if `DASH_MASTER_FINGERPRINT` is unset, or if `DASH_NETWORK` already has a row whose `account_xpub` / `fingerprint` does not match the configured `DASH_XPUB` + `DASH_MASTER_FINGERPRINT`. Switching network on the same Mongo DB is fine (separate `_id`). Mixing an xpub across networks is not.

Address allocation:

```javascript
db.dash_wallet_state.findOneAndUpdate(
  { _id: configured_network },
  { $inc: { next_receive_index: 1 } },
  { returnDocument: "before" }
)
```

Use the returned `next_receive_index` as `n`. Mongo `$inc` is atomic.

### Collection `dash_payouts` (created empty in v1)

Designed now so indexes exist:

| Field | Notes |
|---|---|
| `external_id` | unique |
| `state` | `PENDING` / `BUILDING` / `BROADCAST` / `LOCKED` / `CONFIRMED` / `FAILED` |
| `dest_address`, `duffs`, `fee_duffs`, `change_address`, `change_index` | |
| `inputs[]` | `{txid, vout, duffs, address, path}` |
| `txid` | unique sparse |
| `inputs[].path` | required for coin selection (`address → path` from `dash_invoices` + prior change) |

Optional later: `dash_utxo_locks` `{txid, vout, locked_by, expires_at}` unique on `(txid, vout)`, plus dashd `lockunspent`, so two payout builders cannot pick the same UTXO.

### Scoped Mongo user

Do not authenticate as `v4vapp-dev-user` / `v4vapp-user`. Those roles are `readWrite` + `dbAdmin` on the entire backend DB (can drop `ledger`). Create `v4vapp-dash-user` with a custom role. Companion snippet for backend YAML / `mongosh` (production `rsV` only; `rsPytest` has no auth):

```javascript
use admin
db.createRole({
  role: "dashCollections",
  privileges: ["dash_invoices", "dash_wallet_state", "dash_payouts"].map(c => ({
    resource: { db: "v4vapp-backend", collection: c },
    actions: ["find", "insert", "update", "remove", "createIndex", "listIndexes"]
  })),
  roles: []
})
use v4vapp-backend
db.createUser({
  user: "v4vapp-dash-user",
  pwd: "...",
  roles: [{ role: "dashCollections", db: "admin" }]
})
```

Devdocker equivalent uses db `v4vapp-dev`. Dash creates indexes itself with `createIndex`; it does not need `dbAdmin`.

### Migration strategy

Greenfield collections. No migration of existing `invoices` or `magi_btc`. Startup (see [dashd watch-only bootstrap](#dashd-watch-only-bootstrap)):

1. `create_index` with the names above (idempotent).
2. Load `dash_wallet_state[_id=DASH_NETWORK]`. If missing, insert `{next_receive_index: 0, account_xpub, fingerprint, descriptor_range_end: 100000}`. If present, compare xpub/fingerprint and abort on mismatch.
3. dashd: `createwallet` if needed; `getdescriptorinfo` + `importdescriptors` with checksum and `timestamp: "now"` if the watch wallet has no descriptors yet. Never `rescan` from genesis on `prune=550`.
4. Backend YAML lists the three collections + `v4vapp-dash-user` (documentation + prod user creation). Not required for dash to create indexes.

Companion (out of repo, later): `v4vapp-backend-v2` PR 12 polls this API and, on `SETTLED`/`OVERPAID`, credits **`sats_requested`** via a new `LedgerType` (separate backend accounting design). `UNDERPAID` is not credited. This repo only writes `dash_*` collections.

---

## Conversion, quotes, and payment policy

### sats → duffs

All arithmetic uses `Decimal`. CoinGecko/CMC numbers are ingested as `Decimal(str(n))`, never as binary float.

```
sats_per_dash = SATS_PER_BTC * (dash_usd / btc_usd)
              = SATS_PER_BTC * dash_btc          # CoinGecko dash.btc
duffs_quoted  = ceil(sats_requested * 1e8 / sats_per_dash)
```

Always **ceil** so the customer cannot underpay by rounding. Store both integer amounts and the full quote snapshot.

Worked example (matches the `201` JSON):

- `sats_requested = 25000`
- `dash_btc = 0.00050000` → `sats_per_dash = 100_000_000 * 0.0005 = 50000`
- `duffs_quoted = ceil(25000 * 1e8 / 50000) = 50_000_000` (0.50000000 DASH)
- URI `dash:<addr>?amount=0.50000000`

dashd `getreceivedbyaddress` / `listunspent` return **DASH floats**. Convert:

```python
DUFFS = Decimal("100000000")
duffs = int((Decimal(str(rpc_amount)) * DUFFS).to_integral_value(rounding=ROUND_DOWN))
```

Sources (in order):

1. CoinGecko `https://api.coingecko.com/api/v3/simple/price?ids=dash,bitcoin&vs_currencies=btc,usd` — same host family the backend already uses (`ALL_PRICES_COINGECKO` in `crypto_prices.py`).
2. CoinMarketCap `v1/cryptocurrency/quotes/latest` with `DASH,BTC` if `COINMARKETCAP_API_KEY` is set (backend already has this key in YAML / `ApiKeys.coinmarketcap`).
3. In-memory cache 60 s (`quote.ttl_s`). No Redis dependency (backend Redis is for Hive quotes and notifications; dash must stay self-contained).

If both sources fail → `422 quote_unavailable`. Do not issue an address without a quote.

### Under / over / partial / late

An output is **pre-expiry** if its `detected_at < expires_at` (first seen in mempool or a block before the clock). Amounts below refer to the sum of **pre-expiry** outputs only. New outputs first seen after `expires_at` or after `CANCELED` are recorded on `txids[]` with `late_payment=true` and **never** move the invoice to SETTLED.

| Event | Rule |
|---|---|
| Pre-expiry received ≥ `duffs_quoted` and policy met | `SETTLED` (exact) or `OVERPAID` (surplus). Credit **`sats_requested` only**. Surplus stays in the receive wallet as working capital / fee buffer. |
| Pre-expiry received ≥ `duffs_quoted - tolerance` and policy met | `SETTLED`. Tolerance = `max(underpay_tolerance_duffs, duffs_quoted * bps / 10000)`. Defaults: **100 bps (1%)** and **50_000 duffs (0.0005 DASH)**. |
| Pre-expiry received > 0, below tolerance, `now < expires_at` | Stay `DETECTED`; more outputs to the same address still count. |
| `now ≥ expires_at`, some pre-expiry outputs, sum below tolerance | `UNDERPAID`. **Credit nothing.** Alert. Invoice stays `UNDERPAID`; ops may refund or wait. Funds remain in the wallet. |
| `now ≥ expires_at`, **no** pre-expiry output | `EXPIRED`. |
| First seen after `expires_at` or after `CANCELED` | Leave terminal state (`EXPIRED`/`CANCELED`) or stay `OPEN`→flip `EXPIRED` if the clock already passed. Set `late_payment=true`. Alert. Do not credit. |
| Pre-expiry output exists, policy not yet met | Stay `DETECTED` even after `expires_at`, until policy met or `settle_deadline_at`. After `settle_deadline_at` still waiting: stay `DETECTED`, increment `dash_invoices_stuck_pending_total`, alert. Never downgrade to `EXPIRED`. |
| Multiple UTXOs | Sum **pre-expiry** ones. First moment the sum + policy qualifies wins. |

### Confirmation / InstantSend policy

Two named policies. **Do not** combine them into “IS or CL or 1-conf.”

**`DASH_SETTLE_POLICY=instantsend_or_chainlock`** (default on mainnet and testnet):

1. `instantlock == true` → settle. No confirmation wait.
2. Else `chainlock == true` (dashd `gettransaction` / block ChainLock) → settle.
3. Else **do not settle**. A 1-conf block that is not ChainLocked can reorg; treating it as final makes step 2 dead.
4. If still unlocked at `settle_deadline_at`, stay `DETECTED` and alert. Operators may flip that invoice to `conf_n` manually; v1 does not auto-fall-through.

**`DASH_SETTLE_POLICY=conf_n`** (default on **regtest**; optional emergency on mainnet):

- Settle when `confirmations ≥ DASH_MIN_CONF`.
- Defaults: regtest **1** (we mine); testnet **2**; mainnet **6** (same table Dash uses for InstantSend input qualification when neither IS nor ChainLock is present).

`min_confirmations` on `POST /v1/invoices` overrides `DASH_MIN_CONF` only under `conf_n`.

---

## Wallet and key management

### Key material

| Item | Where | Git? | v1? |
|---|---|---|---|
| Account **xpub** | env `DASH_XPUB` or secret file `/run/secrets/dash_xpub`; also stored in `dash_wallet_state` | **Never** | **Required** |
| BIP39 mnemonic / BIP32 xprv | Docker secret, only if `DASH_PAYOUTS_ENABLED=true` | **Never** | Not loaded |
| `DASH_API_KEY` / `DASH_API_KEY_PREV` | env / secret | Never | Required |
| dashd RPC password | env `DASH_RPC_PASSWORD` | Never | Required |

`example.env` contains placeholders only. Compose mounts `dash_xpub` as a secret. A one-shot CLI `python -m v4vapp_dash.wallet.derive_xpub` (operator laptop, never the server image) reads a mnemonic and prints JSON, then exits:

```json
{
  "network": "mainnet",
  "account_xpub": "xpub…",
  "master_fingerprint": "a1b2c3d4"
}
```

`master_fingerprint` is the BIP32 **master** key fingerprint (first 4 bytes of HASH160(master pubkey)), computed while the mnemonic is in hand. An account xpub does not contain it. The secret file is this JSON (not a bare xpub). Env equivalent: `DASH_XPUB` + `DASH_MASTER_FINGERPRINT` + `DASH_NETWORK`. Boot refuses to start if fingerprint is missing — origin descriptors cannot be built without it.

v1 **does not keep seed material in process memory.** Compromise of the API container yields the xpub (all receive addresses, privacy) and the ability to mark invoices settled incorrectly — not the ability to spend. Spending waits for PR 11 + `DASH_PAYOUTS_ENABLED`.

### Derivation

```python
from bip_utils import Bip44, Bip44Coins, Bip44Changes

coin = Bip44Coins.DASH if network == "mainnet" else Bip44Coins.DASH_TESTNET
acct = Bip44.FromExtendedKey(xpub, coin)              # account-level xpub
recv = acct.Change(Bip44Changes.CHAIN_EXT).AddressIndex(n)
address = recv.PublicKey().ToAddress()                # X… mainnet / y… testnet+regtest
# recv.PrivateKey() is unavailable from xpub — correct for v1
```

Locked paths (PR 4 golden vectors):

| Network | Enum | Path | Address version |
|---|---|---|---|
| mainnet | `Bip44Coins.DASH` | `m/44'/5'/0'/0/n` | `X…` |
| testnet | `Bip44Coins.DASH_TESTNET` | `m/44'/1'/0'/0/n` | `y…` |
| regtest | `Bip44Coins.DASH_TESTNET` | `m/44'/1'/0'/0/n` | `y…` |

One account `0` for v1. No Electrum gap-limit scan: indexes are allocated sequentially and every address is in `dash_invoices`. Descriptor range default `[0, 100000]` (~500 days at 200/day).

**Never reuse an address.** Cancelled and expired addresses stay reserved.

### dashd watch-only bootstrap

Runs on API lifespan (PR 6). Pin `dashpay/dashd` **≥ 21.0**.

**RPC targeting.** Dash Core ≥ 21 does not auto-select a wallet. `createwallet` is a *node* RPC. Every subsequent wallet method (`getwalletinfo`, `importdescriptors`, `listunspent`, `getreceivedbyaddress`, `gettransaction` when wallet-scoped, `lockunspent`) MUST hit the named wallet:

| Kind | URL / flag |
|---|---|
| Node RPCs (`createwallet`, `getblockchaininfo`) | `DASH_RPC_URL` = `http://dashd:9998` |
| Wallet RPCs | `DASH_RPC_URL` + `/wallet/` + `DASH_RPC_WALLET` → `http://dashd:9998/wallet/watch` |

`rpc.py` takes `DASH_RPC_WALLET=watch` (default) and never calls wallet methods on the node root. If `dash_oneshot` also materializes a default wallet, we still only import into `watch`.

1. Node RPC `listwallets` / wallet RPC `getwalletinfo` on `/wallet/watch`. If the wallet is missing:
   ```
   createwallet(                    # node RPC, no /wallet/ path
     wallet_name="watch",
     disable_private_keys=true,
     blank=true,
     passphrase="",
     avoid_reuse=false,
     descriptors=true,
     load_on_startup=true
   )
   ```
2. Build raw descriptor strings (no checksum yet) using `master_fingerprint` from `derive_xpub` (not derived from the account xpub). Mainnet receive example:
   ```
   pkh([FINGERPRINT/44h/5h/0h]xpub…/0/*)
   pkh([FINGERPRINT/44h/5h/0h]xpub…/1/*)
   ```
   Testnet/regtest use `44h/1h/0h` and the testnet xpub version. Do **not** import origin-less `pkh(xpub/0/*)` in v1 — the fingerprint is required so later `signrawtransactionwithkey` paths stay consistent with the descriptor origin.
3. `getdescriptorinfo(desc)` → append `#` + `checksum`.
4. If the **watch** wallet has no matching descriptors (wallet RPC):
   ```
   importdescriptors([
     { "desc": "pkh(…)#cksum", "timestamp": "now", "active": true,
       "internal": false, "range": [0, 100000] },
     { "desc": "pkh(…)#cksum", "timestamp": "now", "active": true,
       "internal": true,  "range": [0, 100000] }
   ])
   ```
   Official fields only: `desc`, `active`, `range`, `next_index` (omit; dashd sets it), `timestamp`, `internal`, `label` (omit). **No `watchonly` key** — watch-only is implied by an xpub descriptor in a `disable_private_keys` wallet.
   **`timestamp: "now"` is mandatory on `prune=550`.** `timestamp: 0` / `rescan: true` fails because pruned history is gone. A brand-new receive service has no historical UTXOs to find.
5. Subsequent boots: `rescan` is not requested. If `next_receive_index > 0.8 * descriptor_range_end`, extend via wallet RPC:
   ```
   importdescriptors([{ "desc": "<same checksummed desc>",
                        "timestamp": "now", "range": [0, new_end],
                        "active": true, "internal": false }])
   ```
   then `$set` `descriptor_range_end`. `503 index_exhausted` only if this RPC fails.

Image / ports (compose, not ad-hoc `command: dashd`):

- Official entrypoint is `dash_oneshot`. Prefer env + a mounted `config/dash.conf` at the image’s data root **`/home/dash/.dashcore/dash.conf`** (or the path `dash_oneshot` actually reads). The file **must** contain `rpcuser`, `rpcpassword`, and `rpcport=9998` so `dash-cli` can authenticate without extra flags.
- Always pass `-rpcport=9998` (overrides mainnet 9998 / testnet 19998 / **regtest 19898**).
- Healthcheck is a **node** RPC (must work before `watch` exists):  
  `dash-cli -rpcport=9998 -rpcuser=$DASH_RPC_USER -rpcpassword=$DASH_RPC_PASSWORD getblockchaininfo`  
  Do **not** pass `-rpcwallet=watch` on the healthcheck — first boot has no wallet yet. Wallet readiness is the API lifespan, not compose `service_healthy`.
- `v4vapp-dash` `depends_on: dashd: condition: service_healthy`.

### Threat model

| Threat | Severity | Mitigation |
|---|---|---|
| Mnemonic leaked via git / image layer | **Critical** | Not in the v1 image. `.dockerignore` + CI grep for `DASH_MNEMONIC=` / `DASH_XPRV=` / `DASH_XPUB=` committed. |
| Mnemonic in API process memory | n/a in v1 | Loaded only if `DASH_PAYOUTS_ENABLED=true` (PR 11). |
| dashd compromised | Medium | dashd is **watch-only** (`disable_private_keys`). No `importprivkey`. |
| Address reuse / chain analysis | Medium | Fresh address per invoice. |
| xpub leakage | Low | Reveals all receive addresses (privacy), not funds. Do not log at INFO. |
| Caller spoofing invoices | High | `X-API-Key` + private network. No public Traefik route. |
| Compromised API writes SETTLED | High | **v1 does not credit** anyone. PR 12 trusts an authenticated `SETTLED`/`OVERPAID` from this API (`X-API-Key` + private network); it does **not** re-query dashd. That is accepted for the first credit path. Independent chain verify (backend checks `txids[]` against its own dashd/Blockbook before `process_dash_receipt`) is a later hardening option, not PR 12. Scoped Mongo user cannot drop `ledger`. |
| Quote manipulation | Medium | Snapshot on create; settlement against `duffs_quoted`. Cache 60 s. |
| Late payment to expired/canceled address | Medium | Permanent unswept watch, flag, do not auto-credit. |
| Descriptor range exhaustion | Low | Default 100k; auto-extend; alert at 80%. |
| Dust / address poisoning | Low | Ignore outputs below `DASH_DUST_DUFFS` (default 5460). |
| 1-conf reorg credited as paid | High | `instantsend_or_chainlock` does **not** fall through to `minconf=1`. |

v1 is a **watch-only receiver**. Treat on-chain balance as working capital, not treasury. Sweep via the future payout API (which is when the process becomes a hot signer).

---

## Watching implementation

Default watcher (`src/v4vapp_dash/watcher/loop.py`). **Poll only in v1.** There is no `DASH_WATCH_MODE=zmq` implementation; that is a later PR.

Each tick:

1. Sleep `DASH_POLL_INTERVAL_S` (default **10**).
2. Load every invoice that is **not swept** (`swept_at == null`) and whose state is in `{OPEN, DETECTED, EXPIRED, CANCELED}`. No 24 h cutoff — a payment on day 3 must still be flagged. Cap a tick at `DASH_WATCH_BATCH` (default 500 addresses); rotate by `_id`.
3. One batched `listunspent(0, 9999999, [addresses], true)`. Parse amounts with `Decimal` → integer duffs.
4. For each new `{txid, vout}`, `gettransaction` for `instantlock` / `chainlock` / `confirmations`.
5. Apply first-seen vs expiry vs policy (above). `find_one_and_update` with a state predicate so two ticks cannot double-settle.
6. If dashd is unreachable and `WATCH_FALLBACK_URL` is set, poll Blockbook `GET /api/v2/address/{addr}?details=txs` and mark health `degraded`. If unset, mark `error` and skip the tick.

The “unexpected residual UTXO” alert is this scan: any `EXPIRED`/`CANCELED` address whose `listunspent` sum exceeds last persisted `duffs_received` increments `dash_invoices_late_payment_total` and logs ERROR. The future payout path sets `swept_at` so those addresses drop out of the watch set.

---

## Outgoing payments (designed)

Not implemented in v1. The following is the contract so v1 schema and wallet layout do not paint us into a corner.

### API

`POST /v1/payouts` (auth required) — see body above.

States: `PENDING → BUILDING → BROADCAST → LOCKED → CONFIRMED | FAILED`.

### Algorithm (not implemented in v1)

1. Convert `sats` → duffs if needed (fresh quote, `Decimal` ceil).
2. Resolve spendable UTXOs from `listunspent` and map each `address → path` via `dash_invoices` (receive) and prior `dash_payouts.change_address` (change). Do not guess paths.
3. Coin-select largest-first until `target + fee`. Prefer InstantSend-**eligible** inputs (already IS-locked or ChainLocked, else ≥6/2 conf). Avoid dust.
4. Reserve selected outpoints: dashd `lockunspent(false, [...])` **and** insert `dash_utxo_locks` with a TTL. One worker is not a lock.
5. Fee: `estimatesmartfee(1)` returns **DASH per kvB**. `fee_duffs = ceil(rate_dash_per_kvb * Decimal(vbytes) / Decimal(1000) * 1e8)` with `vbytes ≈ 148*nIn + 34*nOut + 10`. Since Dash 0.14, qualifying txs are **automatically InstantSend** — do not add a special IS surcharge or an `instantsend: true` request field.
6. Change: `m/44'/5'/0'/1/{next_change_index}` on mainnet (`m/44'/1'/0'/1/n` on testnet/regtest). `$inc` `next_change_index`. Covered by the imported `…/1/*` descriptor.
7. Load mnemonic/xprv **only now** (`DASH_PAYOUTS_ENABLED`). `createrawtransaction` → `signrawtransactionwithkey` (WIFs in RAM for this request) → `sendrawtransaction`. Zero the WIFs.
8. Persist `dash_payouts` with `inputs[].path`. Watch the txid. Unlock leftover UTXOs.
9. Fee bumping: no RBF habit on Dash. Later: CPFP from change, or wait.
10. Consolidation: `POST /v1/payouts/consolidate` sweeps dust to change during low-fee periods.

### Why not `sendtoaddress`

`sendtoaddress` requires the node to hold private keys. That collapses the watch-only threat model. `use_is` on that RPC is leftover UX (automatic IS). We only fall back to a dashd-held wallet if in-process signing is blocked by a script type we cannot build — unlikely for P2PKH.

---

## Docker, compose, and CI

### Dockerfile

Copy the backend / api-ext pattern: `python:3.12` builder + `uv sync --no-dev`, then `python:3.12-slim` runtime, `PATH=/app/.venv/bin:$PATH`.

```
CMD ["uvicorn", "v4vapp_dash.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

Do **not** copy `secrets/` or `.env` into the image.

### `docker-compose.yaml` (sketch)

Default file is **self-contained**: internal network `v4vapp-dash`, local `dashd`, optional local mongo for regtest. A greenfield clone can `docker compose up` without the sibling repo.

```yaml
services:
  v4vapp-dash:
    build: .
    container_name: v4vapp-dash
    env_file: .env
    secrets:
      - dash_xpub
    environment:
      DASH_XPUB_FILE: /run/secrets/dash_xpub
      DASH_RPC_URL: http://dashd:9998
      DASH_RPC_WALLET: watch
    # Production: do not publish ports. Backend uses http://v4vapp-dash:8080.
    # Local/regtest overlay only:
    # ports:
    #   - "127.0.0.1:8088:8080"
    networks: [v4vapp-dash]
    depends_on:
      dashd:
        condition: service_healthy
    restart: on-failure:3
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

  dashd:
    image: dashpay/dashd:22   # pin ≥ 21; digest in prod
    container_name: v4vapp-dashd
    # Official entrypoint is dash_oneshot. Extra flags append.
    command:
      - -server=1
      - -txindex=0
      - -prune=550
      - -rpcport=9998
      - -rpcallowip=10.0.0.0/8
      - -rpcallowip=172.16.0.0/12
      - -rpcbind=0.0.0.0
      - -rpcuser=${DASH_RPC_USER}
      - -rpcpassword=${DASH_RPC_PASSWORD}
    volumes:
      - dashd-data:/home/dash          # image data root, not /.dashcore
      - ./config/dash.conf:/home/dash/.dashcore/dash.conf:ro  # rpcuser/rpcpassword/rpcport for dash-cli
    networks: [v4vapp-dash]
    restart: unless-stopped
    healthcheck:
      # Node RPC — no -rpcwallet (wallet is created by the API on first boot).
      # Prefer dash.conf rpcuser/rpcpassword; compose does not interpolate ${} inside JSON-array healthchecks.
      test: ["CMD", "dash-cli", "-rpcport=9998", "getblockchaininfo"]
      interval: 15s
      timeout: 10s
      retries: 10
      start_period: 30s

networks:
  v4vapp-dash:
    driver: bridge

secrets:
  dash_xpub:
    file: ./secrets/dash_xpub.txt

volumes:
  dashd-data:
```

`docker-compose.regtest.yaml` adds `-regtest`, still `-rpcport=9998`, `DASH_NETWORK=regtest`, `DASH_SETTLE_POLICY=conf_n`, `DASH_MIN_CONF=1`, and (optionally) a no-auth mongo for CI. Fully self-contained.

`docker-compose.backend-net.yaml` is the **optional** overlay to talk to the live backend stack:

```yaml
networks:
  v4vapp-dash:
    name: v4vapp-backend-v2_v4vapp-backend   # confirm via `docker network ls`
    external: true
```

To run **without** a local dashd (Umbrel/Tailscale, same pattern as LND `umbrel.tail400e5.ts.net:10009`):

```
DASH_RPC_URL=http://umbrel.tail400e5.ts.net:9998
DASH_RPC_WALLET=watch
```

and omit the `dashd` service.

### `example.env`

```
DASH_NETWORK=regtest
DASH_API_KEY=change-me-long-random
DASH_API_KEY_PREV=
DASH_BIND=0.0.0.0
DASH_PORT=8080
DASH_DOCS_ENABLED=true

# rsPytest / mongo-pytest-local: NO auth (DBConn strips credentials for rsPytest)
MONGO_URI=mongodb://dot.tail400e5.ts.net:37017/v4vapp-dev?replicaSet=rsPytest
MONGO_DB_NAME=v4vapp-dev
# Production rsV (auth, scoped user):
# MONGO_URI=mongodb://v4vapp-dash-user:SECRET@gad-v4vapp.tail400e5.ts.net:27017,edi-v4vapp.tail400e5.ts.net:27017,dave-v4vapp.tail400e5.ts.net:27017/v4vapp-backend?authSource=v4vapp-backend&replicaSet=rsV

DASH_RPC_URL=http://dashd:9998
DASH_RPC_WALLET=watch
DASH_RPC_USER=dashrpc
DASH_RPC_PASSWORD=change-me
DASH_XPUB_FILE=/run/secrets/dash_xpub
# DASH_XPUB=                    # alternative to the JSON secret
# DASH_MASTER_FINGERPRINT=      # required if DASH_XPUB is set (from derive_xpub)
# DASH_MNEMONIC_FILE=          # only when DASH_PAYOUTS_ENABLED=true

DASH_POLL_INTERVAL_S=10
DASH_SETTLE_POLICY=conf_n      # regtest default; mainnet uses instantsend_or_chainlock
DASH_MIN_CONF=1                # regtest; mainnet fallback is 6 under conf_n
DASH_SETTLE_GRACE_S=3600
DASH_UNDERPAY_BPS=100
DASH_UNDERPAY_DUFFS=50000
DASH_DESCRIPTOR_RANGE_END=100000
DASH_WATCH_BATCH=500
DASH_PAYOUTS_ENABLED=false

COINGECKO_URL=https://api.coingecko.com/api/v3/simple/price
COINMARKETCAP_API_KEY=
WATCH_FALLBACK_URL=
```

### CI

`.github/workflows/ci.yml`:

1. `uv sync` + `ruff` + `mypy` + `pytest tests/unit`.
2. Job `integration` starts `docker-compose.regtest.yaml`, waits for dashd, runs `tests/integration` (derive address, mine to it, watcher marks SETTLED).
3. Secret-scan step: fail if `DASH_MNEMONIC`, `DASH_XPRV`, `DASH_XPUB=xpub`, or a BIP39 wordlist-looking line appears outside `example.env` placeholders.

---

## Quantification

| Quantity | Value | Basis |
|---|---|---|
| Expected create rate | **10–200 invoices/day** (peak ~1/min) | V4V is a niche Lightning/Hive bridge, not a retail processor. Lightning invoice volume in this codebase is operationally small (single LND node, one `api-v2` worker). |
| Concurrent OPEN invoices | **< 50** typical, design for 200 | 15 min expiry × 1/min peak. |
| Poll load | Batched `listunspent` every 10 s ≈ **6 ticks/min** + a few `gettransaction` ≈ **~20 RPC/min** typical; unswept scan adds batches of 500 | Trivial for dashd. |
| Status GET load | Backend 2–5 s × 50 open ≈ **25 req/s peak** | Mongo `_id` read. |
| Address index growth | ≤ 200/day → **~75k/year** | Default descriptor range **100k** lasts >1 year at peak. Alert at 80%; auto-extend. |
| Storage | ~2 KB/invoice → **< 50 MB/year** | Negligible next to `hive_ops` / `ledger`. |
| dashd disk | Pruned **~2–10 GB**; regtest **< 100 MB** | |
| Create latency target | **p99 < 300 ms** excluding first dashd import | Quote cache + one `$inc` + insert. |
| Detect latency target | **≤ poll interval + 1 block / InstantSend lock** (~10 s + 0–2.5 min) | |
| Quote cache | 60 s | DASH/BTC is volatile; 60 s is tighter than backend CoinGecko 360 s because we lock the quote into the invoice. |

---

## Alternatives Considered

### 1. TypeScript service + dashcore-lib, no dashd

Use `@dashevo/dashcore-lib` to derive and sign, and a hosted Blockbook (NowNodes/GetBlock) to watch and broadcast.

- **Pros:** No 10 GB node. Best in-process tx builder. Official JS lib.
- **Cons:** New language in the org. Custodial funds depend on a third-party indexer for correctness. InstantSend/ChainLock flags become vendor-API-specific. Broadcast reliability is out of our hands. Does not match how LND is run (self-hosted Umbrel/Voltage).
- **Why rejected:** Fine as a *fallback watch path*; not as the primary wallet.

### 2. FastAPI + Node sidecar

Python orchestrates Mongo/HTTP; a 50-line Node service wraps dashcore-lib `PrivateKey` / `Transaction`.

- **Pros:** Uses the official L1 lib for signing; Python stays the API.
- **Cons:** Two health domains, two images, local-dev friction, for a workload of tens of invoices/day. `signrawtransactionwithkey` already does the hard part inside dashd.
- **Why rejected for v1:** Revisit if we hit a Dash script / InstantSend payload we cannot build from Python + dashd.

### 3. `getnewaddress` hot wallet inside dashd

Simplest possible receive path.

- **Pros:** Zero HD code. `sendtoaddress` for outgoing.
- **Cons:** Private keys live in `wallet.dat` on the dashd volume. Backup/restore is a Dash Core wallet ritual, not a 12-word mnemonic the operator already understands. Cannot do xpub-only watch. Worse threat model.
- **Why rejected as primary:** We still run dashd, but **watch-only**.

### 4. Dash Platform SDK (`dash` npm) as the integration

- **Pros:** Official "one library" marketing; can send L1 via DAPI.
- **Cons:** Designed for Platform identities/documents. Introduces DAPI availability as a payment dependency. Not how a custodial invoice service should move UTXOs.
- **Why rejected.**

---

## Security & Privacy Considerations

- **Private API only.** No `traefik-public` labels. **No production host port.** Backend reaches `http://v4vapp-dash:8080` on the compose network. Local/regtest may bind `127.0.0.1:8088:8080` (not 8080 — `admin-interface` already owns 8080; never `0.0.0.0`).
- **Auth:** `X-API-Key` with constant-time compare against `DASH_API_KEY` then `DASH_API_KEY_PREV`. `/metrics` uses the same key. `/health` is open.
- **Key handling (v1):** xpub file mode `0600`. Process never reads a mnemonic. Heap dumps disabled. Logs must never print mnemonic, xprv, WIF, xpub at INFO, or `DASH_API_KEY`.
- **Mongo:** `v4vapp-dash-user` can CRUD only `dash_invoices`, `dash_wallet_state`, `dash_payouts`. Cannot drop `ledger` / `invoices`.
- **dashd RPC** bound to the compose network only (`rpcallowip` for Docker bridges). Always `-rpcport=9998`. RPC credentials are not the wallet seed.
- **PII:** `cust_id` and `memo` may contain Hive account names. Same sensitivity as `invoices.memo`. No extra retention rule.
- **Chain privacy:** one address per invoice; do not put `cust_id` in an `OP_RETURN`.
- **Docs:** `/docs` off in production so the OpenAPI surface is not a free recon map.
- **Supply chain:** pin `dashpay/dashd` digest in production compose; `uv.lock` committed.

---

## Observability

JSON logs to stdout (Docker json-file rotation, matching api-ext). Fields:

```
ts, level, event, invoice_id, external_id, address, state, duffs, txid, err
```

Never log secrets or full xprv. `path` and `index` are OK at DEBUG.

**Metrics** (Prometheus text at `/metrics`, **requires `X-API-Key`** — same middleware as `/v1/*`. `/health` stays open for compose):

- `dash_invoices_created_total{network}`
- `dash_invoices_settled_total{reason="instantsend|chainlock|conf"}`
- `dash_invoices_expired_total`
- `dash_invoices_underpaid_total`
- `dash_invoices_late_payment_total`
- `dash_invoices_stuck_pending_total`
- `dash_open_invoices`
- `dash_watcher_tick_seconds`
- `dash_dashd_up`, `dash_dashd_blocks`, `dash_dashd_synced`
- `dash_quote_age_seconds`, `dash_quote_errors_total{source}`
- `dash_next_receive_index`, `dash_descriptor_range_end`

**Alerts** (wire into the existing notification bot later; v1 logs at ERROR which `db-monitor` / notification pipeline can pick up if desired):

| Condition | Severity |
|---|---|
| dashd down > 60 s | page |
| watcher tick age > 3 × poll interval | page |
| late_payment | high |
| underpaid | high |
| next_index > 0.8 × range_end | warn |
| quote source failure > 5 min | high |
| residual UTXO on unswept EXPIRED/CANCELED (watcher scan) | high |

---

## Rollout Plan

1. **Regtest locally** — compose profile, CI green, xpub from the well-known test mnemonic (funds are worthless).
2. **Testnet on Tailscale** — real InstantSend, faucet-funded, `DASH_API_KEY` only known to a dev `api-v2`. No backend crediting yet; operators inspect Mongo `dash_invoices`.
3. **Mainnet shadow** — small working capital, create invoices from a hidden admin path, do not credit customers. Verify InstantSend settle times and descriptor import on pruned dashd.
4. **Backend integration** (separate repo/PR 12): backend-v2 (only client) polls `GET /v1/invoices/{id}` on the Docker network and, on authenticated `SETTLED`/`OVERPAID`, credits **`sats_requested`** via a new `LedgerType` (separate backend accounting design). `UNDERPAID` is not credited. Surplus Dash stays in the receive wallet. PR 12 **trusts** this API; it does not independently re-query dashd. No automatic ledger mirror from this repo.
5. **Later (backend / api-ext, not this repo):** public Lightning-invoice photograph → backend creates a Dash invoice here → on SETTLED the backend pays the Lightning invoice. Frontend/api-ext never call v4vapp-dash.
6. **Payouts PR series** after inbound has been stable.

**Rollback:** stop creating invoices (caller feature flag). Watcher can keep running to settle in-flight OPENs. Image rollback is safe: state is in Mongo + dashd wallet; HD index only moves forward. Never rewind `next_receive_index`.

**Feature flags**

| Flag | Default | Meaning |
|---|---|---|
| `DASH_ENABLED` (caller side) | false | Backend will not call create. |
| `DASH_SETTLE_POLICY` | `instantsend_or_chainlock` (mainnet/testnet); `conf_n` (regtest) | No auto-fall-through from IS/CL to 1-conf. |
| `DASH_MIN_CONF` | 6 / 2 / 1 | Used only under `conf_n`. |
| `DASH_PAYOUTS_ENABLED` | false | 501 + do not load mnemonic. |

---

## Resolved Open Questions

Decided 2026-08-13. These are final.

| # | Question | Decision |
|---|---|---|
| 1 | **v1 caller** | **`v4vapp-backend-v2` only**, on the internal Docker network, **no public port**. Public API / frontend never talk to this module. Later: Dash wallet developers photograph a Lightning invoice and send it to the **public** API; they receive a Dash destination; if paid, the **backend** pays the Lightning invoice. That hop is a later backend/api-ext feature, not a v4vapp-dash endpoint. |
| 2 | **OVERPAID** | Credit **`sats_requested`**. Surplus Dash stays in the receive wallet as working capital / fee buffer. |
| 3 | **UNDERPAID** | Credit **nothing**. Invoice stays `UNDERPAID`; ops can refund or wait. |
| 4 | **Production dashd** | **Local pruned container** next to v4vapp-dash (`dashpay/dashd` in compose, `prune=550`, watch-only descriptors). Independent of Umbrel/LND. |
| 5 | **Automatic ledger mirror** | **No.** This repo only writes `dash_*`. KeepSats/ledger credit is backend PR 12 plus a separate `LedgerType` design. |

---

## Key Decisions

1. **Python FastAPI, not TypeScript.** Team language, sibling-service shape (`v4vapp-api-ext`, `v4vapp-binance`), and dashd RPC make JS unnecessary for v1. `dashcore-lib` is excellent but not required when dashd is in the box.
2. **Do not use the `dash` / DashJS Platform SDK.** It is an L2/DAPI client. This product is L1 UTXO invoices.
3. **HD derivation in-process with `bip_utils`.** Mainnet `Bip44Coins.DASH` → `m/44'/5'/0'`. Testnet **and** regtest `Bip44Coins.DASH_TESTNET` → `m/44'/1'/0'`, `y…` addresses. Do not call `getnewaddress`.
4. **v1 process is watch-only.** Load `DASH_XPUB` only. Mnemonic/xprv enter the API process only when `DASH_PAYOUTS_ENABLED=true` (PR 11).
5. **dashd is a descriptor watch-only wallet named `watch`.** Node `createwallet` then wallet RPCs on `/wallet/watch`. `getdescriptorinfo` + checksummed `importdescriptors` (`desc`/`active`/`range`/`timestamp`/`internal` only — no `watchonly`) with `timestamp: "now"` (prune-safe). Origin uses `master_fingerprint` from `derive_xpub`. Range `[0, 100000]`. Private keys never enter `wallet.dat`.
6. **Polling only in v1** (10 s), matching the user's lean and matching how V4V already *consumes* invoice status (`GET /v1/check_invoice`). ZMQ is a later PR, not a v1 flag.
7. **Same Mongo database, new collections** (`dash_invoices`, `dash_wallet_state` keyed by **network**, later `dash_payouts`). Dedicated `v4vapp-dash-user` with CRUD only on those collections. No Python imports from `v4vapp-backend-v2`.
8. **Amounts: integer sats in, integer duffs stored, quote snapshot on the invoice.** All dashd amounts via `Decimal`. Conversion uses CoinGecko then CMC; does not write `rates_ts`.
9. **State machine extends `InvoiceState`** with `DETECTED`, `UNDERPAID`, `OVERPAID`, `EXPIRED` because UTXO partials and late pays exist on-chain and do not exist on Lightning invoices.
10. **First-seen freeze.** `expires_at` stops *new* outputs; `settle_deadline_at` keeps waiting for IS/CL/`conf_n` on outputs already seen. Late = first seen after `expires_at` (or after cancel). A 14-minute mempool tx on a 15-minute invoice is not late when it confirms at T+16m.
11. **`instantsend_or_chainlock` does not fall through to 1-conf.** 1-conf without ChainLock can reorg. Mainnet/testnet default is IS or CL only. `conf_n` is a separate policy (regtest `minconf=1`; mainnet fallback **6**; testnet **2**).
12. **Auth: private Docker network + `X-API-Key`.** Only caller is backend-v2. `/metrics` keyed; `/health` open; `/docs` off in prod. **No production host port.** Local/regtest may bind `127.0.0.1:8088`.
13. **Outgoing is designed** (raw tx + `signrawtransactionwithkey` + change `…/1/n` + `lockunspent` + `address→path` + automatic InstantSend) and returns 501 until a later PR.
14. **Never reuse addresses. Never TTL-delete.** Watch all unswept `OPEN|DETECTED|EXPIRED|CANCELED` addresses, not a 24 h window.
15. **Working capital, not treasury.** OVERPAID surplus stays in the receive wallet as fee buffer. v1 cannot spend. Sweep path is the payout API.
16. **v1 caller is backend-v2 only.** Public Lightning-invoice → Dash-address hop is a later backend/api-ext feature. This repo never exposes that endpoint.
17. **Credit policy (decided):** OVERPAID → credit `sats_requested`, keep surplus. UNDERPAID → credit nothing.
18. **Production dashd is a local pruned compose service** (`prune=550`), not Umbrel.
19. **No automatic ledger write from this repo.** PR 12 + `LedgerType` live in backend-v2.

---

## References

- Backend entry: `/Users/bol/Documents/dev/v4vapp/v4vapp-backend-v2/src/api_v2.py`
- Mongo: `/Users/bol/Documents/dev/v4vapp/v4vapp-backend-v2/src/v4vapp_backend_v2/database/db_pymongo.py` (`DBConn`)
- Config models: `/Users/bol/Documents/dev/v4vapp/v4vapp-backend-v2/src/v4vapp_backend_v2/config/setup.py` (`InternalConfig`, `DbsConfig`, `CollectionConfig`)
- Dev DB + collections: `/Users/bol/Documents/dev/v4vapp/v4vapp-backend-v2/config/devdocker.config.yaml`
- Prod DB: `/Users/bol/Documents/dev/v4vapp/v4vapp-backend-v2/config/production.fromhome.config.yaml` (`v4vapp-backend`, replica set `rsV`)
- Invoice model: `/Users/bol/Documents/dev/v4vapp/v4vapp-backend-v2/src/v4vapp_backend_v2/models/invoice_models.py`
- Invoice processing: `/Users/bol/Documents/dev/v4vapp/v4vapp-backend-v2/src/v4vapp_backend_v2/process/process_invoice.py`
- LND monitor + expiry prune: `/Users/bol/Documents/dev/v4vapp/v4vapp-backend-v2/src/lnd_monitor_v2.py`, `database/db_tools.py:delete_expired_unsettled_invoices`
- Quotes: `/Users/bol/Documents/dev/v4vapp/v4vapp-backend-v2/src/v4vapp_backend_v2/helpers/crypto_prices.py` (`SATS_PER_BTC`, `AllQuotes`, `rates_ts`)
- Conversion: `/Users/bol/Documents/dev/v4vapp/v4vapp-backend-v2/src/v4vapp_backend_v2/helpers/crypto_conversion.py`
- Currency enum: `/Users/bol/Documents/dev/v4vapp/v4vapp-backend-v2/src/v4vapp_backend_v2/helpers/currency_class.py`
- MAGI analogue: `/Users/bol/Documents/dev/v4vapp/v4vapp-backend-v2/src/v4vapp_backend_v2/magi/magi_classes.py` (`DB_MAGI_BTC_COLLECTION = "magi_btc"`)
- Fixed quotes: `/Users/bol/Documents/dev/v4vapp/v4vapp-backend-v2/src/v4vapp_backend_v2/fixed_quote/fixed_quote_class.py`
- Transaction flow: `/Users/bol/Documents/dev/v4vapp/v4vapp-backend-v2/docs/transaction_flow.md`
- Hive memos: `/Users/bol/Documents/dev/v4vapp/v4vapp-backend-v2/docs/hive_transfers.md`
- Compose: `/Users/bol/Documents/dev/v4vapp/v4vapp-backend-v2/docker-compose.yaml`
- Public invoice API: `/Users/bol/Documents/dev/v4vapp/v4vapp-api-ext/src/v4vapp_api_ext/invoices/invoices_routers.py` (`MAX_INVOICE_TIME = 1800`)
- LND adapter: `/Users/bol/Documents/dev/v4vapp/v4vapp-api-ext/src/v4vapp_api_ext/helpers/lightning_node.py`
- External Traefik: `/Users/bol/Documents/dev/v4vapp/v4vapp-ext-traefik/docker-compose.yaml`
- Dashcore-lib: https://github.com/dashpay/dashcore-lib
- Dash SDK page: https://docs.dash.org/en/stable/docs/user/developers/integration-sdks.html
- Dash wallet RPC: https://docs.dash.org/en/stable/docs/core/api/remote-procedure-calls-wallet.html
- InstantSend: https://docs.dash.org/en/stable/docs/core/guide/dash-features-instantsend.html
- Insight: https://docs.dash.org/en/stable/docs/user/developers/insight.html
- dashpay/dashd image: https://hub.docker.com/r/dashpay/dashd
- bip_utils Dash coin types: https://pypi.org/project/bip-utils/
- SLIP-44 coin type 5: https://github.com/satoshilabs/slips/blob/master/slip-0044.md

---

## PR Plan

Each PR is independently reviewable and mergeable. Later PRs may sit unmerged until the previous one is on `main`.

### PR 1 — Repository skeleton

- **Title:** `chore: bootstrap v4vapp-dash repo (uv, FastAPI hello, Docker, CI)`
- **Files:** `pyproject.toml`, `uv.lock`, `README.md`, `LICENSE`, `Dockerfile`, `docker-compose.yaml` (self-contained internal network `v4vapp-dash`, **no published ports** in the default file), `docker-compose.backend-net.yaml` (join backend network), `docker-compose.regtest.yaml` (optional `127.0.0.1:8088:8080`), `example.env`, `.gitignore`, `.dockerignore`, `.github/workflows/ci.yml`, `src/v4vapp_dash/main.py` (`GET /health`), `src/v4vapp_dash/config.py`, `tests/unit/test_health.py`
- **Depends on:** none
- **Description:** Empty-repo bootstrap matching backend/api-ext Docker+uv style. `docker compose up` works without the sibling network. No secrets, no Dash code. CI runs unit tests.

### PR 2 — Config, Mongo client, indexes, scoped user

- **Title:** `feat: Mongo connection, network-keyed wallet state, dash_* indexes`
- **Files:** `src/v4vapp_dash/db/mongo.py`, `src/v4vapp_dash/db/indexes.py`, `src/v4vapp_dash/config.py`, `tests/unit/test_indexes.py`, README Mongo section (rsPytest **no-auth** URI vs rsV auth + `v4vapp-dash-user` YAML/`mongosh` snippet)
- **Depends on:** PR 1
- **Description:** `AsyncMongoClient` from `MONGO_URI` / `MONGO_DB_NAME`. Indexes for `dash_invoices`, `dash_wallet_state` (`_id` = network), `dash_payouts`. Abort boot on xpub/network mismatch. No backend package imports.

### PR 3 — Auth middleware and API error envelope

- **Title:** `feat: X-API-Key auth and standard error JSON`
- **Files:** `src/v4vapp_dash/api/deps.py`, `src/v4vapp_dash/api/errors.py`, tests
- **Depends on:** PR 1
- **Description:** Constant-time API key, 401/403, `{error: {code, message}}`. `/health` open. `/metrics` and `/v1/*` keyed. Dual-key rotation via `DASH_API_KEY_PREV`.

### PR 4 — HD wallet from xpub (no node, no mnemonic)

- **Title:** `feat: BIP44 Dash address derivation from xpub via bip_utils`
- **Files:** `src/v4vapp_dash/wallet/hd.py`, `src/v4vapp_dash/wallet/derive_xpub.py` (offline CLI), `src/v4vapp_dash/models/wallet.py`, `tests/unit/test_hd.py`
- **Depends on:** PR 1
- **Description:** Load **xpub + master fingerprint** from the JSON secret (or `DASH_XPUB` + `DASH_MASTER_FINGERPRINT`). Mainnet `m/44'/5'/0'/0/n` → `X…`; testnet **and** regtest `m/44'/1'/0'/0/n` → `y…`. Golden vectors. Offline CLI `derive_xpub` prints `{network, account_xpub, master_fingerprint}` from a mnemonic and exits. Atomic `$inc` on `dash_wallet_state[_id=network]`. Never log WIF (none in v1).

### PR 5 — Quote service (sats → duffs)

- **Title:** `feat: DASH quote service (CoinGecko + CMC fallback)`
- **Files:** `src/v4vapp_dash/quotes/service.py`, `src/v4vapp_dash/models/quote.py`, `tests/unit/test_quotes.py`
- **Depends on:** PR 1
- **Description:** Fetch dash/btc/usd as `Decimal`, cache 60 s, ceil sats→duffs. Golden test: `25000` sats × `dash_btc=0.0005` → `50_000_000` duffs. No writes to `rates_ts`.

### PR 6 — dashd RPC, watch-only bootstrap, regtest compose

- **Title:** `feat: dashd RPC, createwallet + checksummed descriptors, regtest compose`
- **Files:** `src/v4vapp_dash/dashd/rpc.py`, `src/v4vapp_dash/dashd/bootstrap.py`, `src/v4vapp_dash/wallet/descriptors.py`, `src/v4vapp_dash/dashd/blockbook.py` (fallback client), `docker-compose.yaml`, `docker-compose.regtest.yaml`, `tests/integration/test_dashd_regtest.py`, `tests/unit/test_bootstrap.py`
- **Depends on:** PRs 1, 4
- **Description:** Pin `dashpay/dashd` ≥ 21, official `dash_oneshot` + volume `/home/dash`, `-rpcport=9998` on every network, `dash.conf` with `rpcuser`/`rpcpassword`/`rpcport`. Node healthcheck: `dash-cli -rpcport=9998 -rpcuser=… -rpcpassword=… getblockchaininfo` (**no** `-rpcwallet`). `depends_on: service_healthy`. `rpc.py`: node RPCs on `DASH_RPC_URL`; wallet RPCs on `DASH_RPC_URL/wallet/${DASH_RPC_WALLET}` (`watch`). Bootstrap: node `createwallet(watch, disable_private_keys, blank, descriptors)` → wallet `getdescriptorinfo` → wallet `importdescriptors` receive `…/0/*` and change `…/1/*` with BIP380 checksum, range `[0, 100000]`, `timestamp: "now"`, **no `watchonly` field**. Origin uses `master_fingerprint` from `derive_xpub`. Decimal conversion helper for RPC amounts. Blockbook HTTP client (used in PR 8). Integration job mines a block on regtest.

### PR 7 — Invoice model + create/get/cancel API

- **Title:** `feat: POST/GET/cancel /v1/invoices`
- **Files:** `src/v4vapp_dash/models/invoice.py`, `src/v4vapp_dash/api/v1/invoices.py`, tests
- **Depends on:** PRs 2, 3, 4, 5, 6
- **Description:** Create path: auth → quote → `$inc` index → derive address → insert `OPEN` with `expires_at` **and** `settle_deadline_at` → 201. Idempotent `external_id`. `GET` by id and by external id. List cursor = `(created_at, _id)`. Cancel only if `OPEN` and `first_seen_at is null`.

### PR 8 — Watcher poll loop and settlement policy

- **Title:** `feat: poll dashd and settle/expire invoices`
- **Files:** `src/v4vapp_dash/watcher/loop.py`, `src/v4vapp_dash/dashd/watch.py`, tests/unit + regtest integration
- **Depends on:** PR 7
- **Description:** 10 s poll of **all unswept** `OPEN|DETECTED|EXPIRED|CANCELED` (no 24 h window). First-seen freeze; late = first seen after `expires_at` or cancel. Policies: `instantsend_or_chainlock` (no 1-conf fall-through) and `conf_n`. Decimal RPC amounts. Blockbook failover. Fixtures **required**: InstantSend lock; 1-conf **without** ChainLock must **not** settle under IS/CL policy; first-seen before expiry then confirm after expiry → SETTLED; first-seen after expiry → late, not SETTLED; cancel then pay → late on CANCELED; underpay at expiry; auto-extend descriptor range. No ZMQ.

### PR 9 — Observability, metrics, hardening

- **Title:** `feat: Prometheus metrics, structured logs, secret-scan CI`
- **Files:** `src/v4vapp_dash/logging.py`, metrics middleware, `.github/workflows/ci.yml`, README ops section
- **Depends on:** PR 8
- **Description:** `/metrics` API-key protected. JSON logs. CI grep for mnemonic/xprv/xpub. Prod notes: **no host port**, docs off, local pruned `dashd`, scoped Mongo user.

### PR 10 — Payouts stub + outgoing design lock-in

- **Title:** `feat: dash_payouts collection and POST /v1/payouts 501 stub`
- **Files:** `src/v4vapp_dash/api/v1/payouts.py`, index ensure for `dash_payouts`, README outgoing section
- **Depends on:** PR 2, PR 7
- **Description:** Collection/indexes and a documented 501 so callers can plan. Documents `address→path`, `lockunspent`, `estimatesmartfee` DASH/kvB, automatic InstantSend. No signing.

### PR 11 (later series) — Implement outgoing payouts

- **Title:** `feat: UTXO selection, change addresses, signrawtransactionwithkey`
- **Files:** `src/v4vapp_dash/wallet/select.py`, `src/v4vapp_dash/wallet/sign.py`, payouts API, `dash_utxo_locks`, watcher for payout txids, integration tests
- **Depends on:** PRs 8, 10
- **Description:** First real spend path. Loads mnemonic only when `DASH_PAYOUTS_ENABLED=true`. Change on `…/1/n`. Not part of the v1 launch bar.

### PR 12 (later, other repo) — Backend consumer

- **Title:** (`v4vapp-backend-v2`) `feat: poll v4vapp-dash and credit SETTLED invoices`
- **Files:** new client in backend, `process_dash_receipt`, new `LedgerType` design, YAML collection + `v4vapp-dash-user` docs, feature flag
- **Depends on:** this repo through PR 8
- **Description:** Out of this repository. Backend-v2 is the only client (Docker DNS, no public dash port). On `SETTLED`/`OVERPAID` credit **`sats_requested`** only; surplus stays in the Dash receive wallet. `UNDERPAID` is not credited. Trusts authenticated responses; does **not** re-query dashd. Does **not** live in v4vapp-dash — this repo never writes `ledger`. Public Lightning-invoice → Dash-address hop is a still-later api-ext/backend feature that calls this same private API through backend-v2.

ZMQ watch mode is **not** a v1 PR. Add it later if poll latency is a problem.

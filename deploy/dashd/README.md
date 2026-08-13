# yoga-dashd — pruned Dash mainnet + testnet

Lives on yoga at `/home/bol/code/dashd`. Two containers, two volumes. Mainnet IBD is independent of testnet.

| Service | Network | RPC | P2P | Volume |
|---|---|---|---|---|
| `dashd` | mainnet | 9998 | 9999 | `dashd-data` |
| `dashd-testnet` | testnet | 19998 | 19999 | `dashd-testnet-data` |

Ports are published on `127.0.0.1` and yoga’s Tailscale IP only (`LOCAL_TAILSCALE_IP` in `.env`). Not on `0.0.0.0`.

This is the **Dash** chain, not Bitcoin. Prune target ~2.2 GiB of block files per network (budget **8–10 GB** for mainnet plus a few GB for testnet, **2 GB RAM** each).

## Start

On yoga:

```bash
cd /home/bol/code/dashd
# .env already has RPC password + LOCAL_TAILSCALE_IP

docker compose up -d                 # both
docker compose up -d dashd-testnet   # testnet only — does not touch mainnet volume
docker compose logs -f dashd-testnet
```

From this repo, after editing compose:

```bash
scp deploy/dashd/docker-compose.yml yoga-v4vapp:/home/bol/code/dashd/
ssh yoga-v4vapp 'cd /home/bol/code/dashd && docker compose up -d'
```

`docker compose up -d` recreates only services whose config changed. Existing `dashd-data` is kept.

## Is it synced?

Mainnet:

```bash
docker compose exec dashd sh -c \
  'dash-cli -rpcport=9998 -rpcuser="$DASH_RPC_USER" -rpcpassword="$DASH_RPC_PASSWORD" getblockchaininfo'
```

Testnet (`-testnet` is required so dash-cli uses the testnet datadir):

```bash
docker compose exec dashd-testnet sh -c \
  'dash-cli -testnet -rpcport=19998 -rpcuser="$DASH_RPC_USER" -rpcpassword="$DASH_RPC_PASSWORD" getblockchaininfo'
```

Watch `verificationprogress` → `1` and `initialblockdownload` → `false`. Testnet `chain` should be `test`.

v4vapp-dash on another Tailscale host:

```
DASH_NETWORK=testnet
DASH_RPC_URL=http://100.83.149.118:19998
DASH_RPC_USER=...
DASH_RPC_PASSWORD=...
DASH_RPC_WALLET=watch
```

## Dash-specific prune rules

- Automatic prune target must be **> 945 MiB** (`prune=550` is Bitcoin and will be rejected).
- Dash defaults to `txindex=1`. Prune requires `-txindex=0`.
- Prune requires `-disablegovernance=1`. InstantSend and ChainLocks still work.
- Turning prune **off** later means re-downloading that chain.

Wallet is **on** (`-disablewallet=0`) so v4vapp-dash can `createwallet watch`. Private keys never go into dashd; the API imports an xpub descriptor.

## Stop / wipe

```bash
docker compose down                         # keeps both volumes
docker compose down dashd-testnet           # stop testnet only
docker volume rm dashd_dashd-testnet-data   # wipe testnet chain only (name may be prefixed)
docker compose down -v                      # deletes BOTH chains — do not do this
```

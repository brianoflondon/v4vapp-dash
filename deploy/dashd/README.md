# yoga-dashd — pruned Dash mainnet node

Take this folder to `yoga-v4vapp` and start it. It only syncs the **Dash** chain (not Bitcoin). A full Dash node is about 40 GB; this prune target keeps roughly 2–3 GB of block files (budget **8–10 GB** disk and **2 GB RAM**).

## Start

```bash
scp -r deploy/yoga-dashd yoga-v4vapp:~/yoga-dashd
ssh yoga-v4vapp
cd ~/yoga-dashd
cp .env.example .env
# put a long random password in .env
docker compose up -d
docker compose logs -f
```

If you already started the broken compose (`exec: -s: invalid option`), copy the updated file over and recreate. The volume is empty enough to keep:

```bash
docker compose up -d --force-recreate
```

First start pulls `dashpay/dashd:23.1.8` and begins initial block download. That can take hours to a day depending on uplink and whether port 9999 is reachable inbound.

## Is it synced?

```bash
docker compose exec dashd sh -c 'dash-cli -rpcport=9998 -rpcuser="$DASH_RPC_USER" -rpcpassword="$DASH_RPC_PASSWORD" getblockchaininfo'
```

Watch `verificationprogress` approach `1`, and `initialblockdownload` become `false`. `pruneheight` will start rising once block files pass the 2200 MiB target.

```bash
docker compose exec dashd dash-cli getnetworkinfo   # connections
docker compose exec dashd dash-cli getblockcount
```

## Ports

| Port | Bind | Why |
|---|---|---|
| 9999/tcp | `0.0.0.0` | Dash P2P. Open this for faster sync. |
| 9998/tcp | `127.0.0.1` | JSON-RPC. Local `dash-cli` only for now. |

Do **not** publish 9998 on the public internet. When `v4vapp-dash` needs this node from another Tailscale host, change the RPC publish line in `docker-compose.yml` to the yoga Tailscale IP.

## Dash-specific prune rules

These are why this is not a copy-paste of a Bitcoin pruned node:

- Automatic prune target must be **> 945 MiB** (`prune=550` is Bitcoin and will be rejected).
- Dash defaults to `txindex=1`. Prune requires `-txindex=0`.
- Prune requires `-disablegovernance=1` (no proposal/voting validation). InstantSend and ChainLocks still work.
- Turning prune **off** later means re-downloading the whole chain.

Wallet is off (`DISABLEWALLET=1`). Sync does not need one. The watch-only descriptor wallet comes later, when v4vapp-dash is ready.

## Stop / wipe

```bash
docker compose down          # keeps the chain volume
docker compose down -v       # deletes the chain; next up is a full resync
```

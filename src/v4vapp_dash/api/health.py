from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

from v4vapp_dash import __version__
from v4vapp_dash.config import get_settings
from v4vapp_dash.dashd.rpc import Dashd
from v4vapp_dash.db.mongo import Mongo
from v4vapp_dash.db.wallet_state import load_wallet_state
from v4vapp_dash.watcher.loop import WatcherState

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    settings = get_settings()
    mongo_ok: bool | None = None
    wallet: dict[str, Any] | None = None
    dashd_info: dict[str, Any] | None = None
    mongo: Mongo | None = getattr(request.app.state, "mongo", None)
    dashd: Dashd | None = getattr(request.app.state, "dashd", None)
    watcher: WatcherState | None = getattr(request.app.state, "watcher", None)

    if mongo is not None:
        try:
            await mongo.ping()
            mongo_ok = True
            state = await load_wallet_state(mongo.db, settings.dash_network)
            if state is not None:
                wallet = {
                    "network": state.get("network"),
                    "next_receive_index": int(state.get("next_receive_index", 0)),
                    "descriptor_range_end": int(state.get("descriptor_range_end", 0)),
                }
        except Exception:
            mongo_ok = False
    elif settings.mongo_uri:
        mongo_ok = False

    if dashd is not None:
        try:
            info = await dashd.getblockchaininfo()
            ibd = bool(info.get("initialblockdownload"))
            dashd_info = {
                "chain": info.get("chain"),
                "blocks": info.get("blocks"),
                "headers": info.get("headers"),
                "verificationprogress": info.get("verificationprogress"),
                "initialblockdownload": ibd,
                "pruned": info.get("pruned"),
                "synced": not ibd,
            }
        except Exception:
            dashd_info = {"error": True}

    watcher_info: dict[str, Any] | None = None
    if watcher is not None:
        age = None
        if watcher.last_tick_at is not None:
            age = (datetime.now(UTC) - watcher.last_tick_at).total_seconds()
        watcher_info = {
            "last_tick_age_s": age,
            "open_invoices": watcher.open_invoices,
            "last_error": watcher.last_error,
        }

    rpc_wanted = bool(
        settings.dash_rpc_url
        and settings.dash_rpc_password
        and settings.dash_rpc_password != "change-me"
    )
    if mongo_ok is False or (dashd_info is not None and dashd_info.get("error")):
        status = "error"
    elif dashd_info is not None and dashd_info.get("initialblockdownload"):
        status = "degraded"
    elif rpc_wanted and dashd is None:
        status = "degraded"
    elif watcher is not None and watcher.last_error:
        status = "degraded"
    else:
        status = "ok"

    body: dict[str, Any] = {
        "status": status,
        "version": __version__,
        "network": settings.dash_network,
        "mongo": mongo_ok,
    }
    if wallet is not None:
        body["wallet"] = wallet
    if dashd_info is not None:
        body["dashd"] = dashd_info
    if watcher_info is not None:
        body["watcher"] = watcher_info
    return body


@router.get("/")
async def root() -> dict[str, str]:
    return {"service": "v4vapp-dash", "version": __version__}

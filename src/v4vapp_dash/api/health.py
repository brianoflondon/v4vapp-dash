from typing import Any

from fastapi import APIRouter, Request

from v4vapp_dash import __version__
from v4vapp_dash.config import get_settings
from v4vapp_dash.db.mongo import Mongo
from v4vapp_dash.db.wallet_state import load_wallet_state

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    settings = get_settings()
    mongo_ok: bool | None = None
    wallet: dict[str, Any] | None = None
    mongo: Mongo | None = getattr(request.app.state, "mongo", None)
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

    if mongo_ok is False:
        status = "error"
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
    return body


@router.get("/")
async def root() -> dict[str, str]:
    return {"service": "v4vapp-dash", "version": __version__}

from fastapi import APIRouter

from v4vapp_dash import __version__
from v4vapp_dash.config import get_settings

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "network": settings.dash_network,
    }


@router.get("/")
async def root() -> dict[str, str]:
    return {"service": "v4vapp-dash", "version": __version__}

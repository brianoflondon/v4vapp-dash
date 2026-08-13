from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from v4vapp_dash import __version__
from v4vapp_dash.api.deps import require_api_key
from v4vapp_dash.api.errors import register_exception_handlers
from v4vapp_dash.api.health import router as health_router
from v4vapp_dash.config import get_settings
from v4vapp_dash.db.indexes import ensure_indexes
from v4vapp_dash.db.mongo import Mongo
from v4vapp_dash.db.wallet_state import ensure_wallet_state


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    mongo: Mongo | None = None
    if settings.mongo_uri:
        mongo = Mongo(settings.mongo_uri, settings.mongo_db_name)
        await mongo.ping()
        await ensure_indexes(mongo.db)
        if settings.dash_xpub and settings.dash_master_fingerprint:
            await ensure_wallet_state(
                mongo.db,
                network=settings.dash_network,
                account_xpub=settings.dash_xpub,
                fingerprint=settings.dash_master_fingerprint,
                descriptor_range_end=settings.dash_descriptor_range_end,
            )
    app.state.mongo = mongo
    try:
        yield
    finally:
        if mongo is not None:
            await mongo.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="v4vapp-dash",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.dash_docs_enabled else None,
        redoc_url="/redoc" if settings.dash_docs_enabled else None,
        openapi_url="/openapi.json" if settings.dash_docs_enabled else None,
    )
    register_exception_handlers(app)
    app.include_router(health_router)

    @app.get("/metrics")
    async def metrics(_key: str = Depends(require_api_key)) -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()

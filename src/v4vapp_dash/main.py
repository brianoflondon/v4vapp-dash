from fastapi import Depends, FastAPI

from v4vapp_dash import __version__
from v4vapp_dash.api.deps import require_api_key
from v4vapp_dash.api.errors import register_exception_handlers
from v4vapp_dash.api.health import router as health_router
from v4vapp_dash.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="v4vapp-dash",
        version=__version__,
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

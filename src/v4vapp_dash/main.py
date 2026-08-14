import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request

from v4vapp_dash import __version__
from v4vapp_dash.api.deps import require_api_key
from v4vapp_dash.api.errors import register_exception_handlers
from v4vapp_dash.api.health import router as health_router
from v4vapp_dash.api.v1.invoices import router as invoices_router
from v4vapp_dash.api.v1.payouts import router as payouts_router
from v4vapp_dash.config import get_settings
from v4vapp_dash.dashd.bootstrap import bootstrap_watch_wallet
from v4vapp_dash.dashd.rpc import Dashd
from v4vapp_dash.db.indexes import ensure_indexes
from v4vapp_dash.db.mongo import Mongo
from v4vapp_dash.db.wallet_state import ensure_wallet_state
from v4vapp_dash.keys import load_xpub_material
from v4vapp_dash.logging import logger, setup_logging
from v4vapp_dash.watcher.loop import WatcherState, run_watcher

QUIET_EXACT = {"/health", "/"}


def _quiet_success(method: str, path: str) -> bool:
    if path in QUIET_EXACT:
        return True
    if method == "GET" and (
        path.startswith("/v1/invoices/by-external/")
        or (path.startswith("/v1/invoices/") and path.count("/") == 3)
    ):
        return True  # GET /v1/invoices/{id} — not list GET /v1/invoices
    return False


def _access_extra(request: Request, status: int, duration_ms: float) -> dict:
    extra: dict = {
        "method": request.method,
        "path": request.url.path,
        "status": status,
        "duration_ms": duration_ms,
    }
    for key in ("invoice_id", "external_id", "cust_id"):
        val = getattr(request.state, key, None)
        if val is not None:
            extra[key] = val
    if "invoice_id" in request.path_params:
        extra.setdefault("invoice_id", request.path_params["invoice_id"])
    if "external_id" in request.path_params:
        extra.setdefault("external_id", request.path_params["external_id"])
    return extra


def _rpc_configured(password: str, url: str) -> bool:
    if not url or not password or password == "change-me":
        return False
    return True


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings)
    logger.info(
        "dash api started",
        extra={"network": settings.dash_network, "version": __version__},
    )
    material = load_xpub_material(settings)

    mongo: Mongo | None = None
    if settings.mongo_uri:
        mongo = Mongo(settings.mongo_uri, settings.mongo_db_name)
        await mongo.ping()
        await ensure_indexes(mongo.db)
        if material is not None:
            await ensure_wallet_state(
                mongo.db,
                network=settings.dash_network,
                account_xpub=material.account_xpub,
                fingerprint=material.master_fingerprint,
                descriptor_range_end=settings.dash_descriptor_range_end,
            )
    else:
        logger.info("mongo disabled")
    app.state.mongo = mongo

    dashd: Dashd | None = None
    if _rpc_configured(settings.dash_rpc_password, settings.dash_rpc_url):
        dashd = Dashd(
            settings.dash_rpc_url,
            user=settings.dash_rpc_user,
            password=settings.dash_rpc_password,
            wallet=settings.dash_rpc_wallet,
        )
        await dashd.getblockchaininfo()
        if material is not None:
            await bootstrap_watch_wallet(
                dashd,
                network=settings.dash_network,
                account_xpub=material.account_xpub,
                fingerprint=material.master_fingerprint,
                range_end=settings.dash_descriptor_range_end,
            )
    else:
        logger.info("dashd rpc not configured")
    app.state.dashd = dashd

    watcher = WatcherState()
    app.state.watcher = watcher
    stop = asyncio.Event()
    task: asyncio.Task[None] | None = None
    if mongo is not None and dashd is not None:
        task = asyncio.create_task(
            run_watcher(mongo=mongo, dashd=dashd, settings=settings, state=watcher, stop=stop)
        )

    try:
        yield
    finally:
        stop.set()
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if dashd is not None:
            await dashd.aclose()
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

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        start = time.perf_counter()
        response = None
        err: BaseException | None = None
        try:
            response = await call_next(request)
            return response
        except Exception as exc:
            err = exc
            raise
        finally:
            try:
                duration_ms = round((time.perf_counter() - start) * 1000, 1)
                status = 500 if err is not None else response.status_code  # type: ignore[union-attr]
                extra = _access_extra(request, status, duration_ms)
                if status >= 500:
                    level = logging.ERROR
                elif status >= 400:
                    level = logging.INFO
                elif _quiet_success(request.method, request.url.path):
                    level = logging.DEBUG
                else:
                    level = logging.INFO
                logger.log(level, "request", extra=extra)
            except Exception:
                pass  # never hide the response / re-raise

    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(invoices_router)
    app.include_router(payouts_router)

    @app.get("/metrics")
    async def metrics(_key: str = Depends(require_api_key)) -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()

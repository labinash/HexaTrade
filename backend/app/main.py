import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.api.routes import router
from app.api.trading import router as trading_router
from app.core.config import get_settings
from app.core.security import install_log_filter
from app.db.base import Base
from app.db.bootstrap import disarm, seed
from app.db.session import get_engine, init_engine, session_factory
from app.services.engine import AppRuntime
from app.workers.historical_loop import historical_loop
from app.workers.market_loop import market_loop

log = logging.getLogger("hextrade")


@asynccontextmanager
async def lifespan(app: FastAPI):
    install_log_filter()
    settings = get_settings()
    runtime = AppRuntime(settings)
    app.state.runtime = runtime
    try:
        init_engine(settings)
        Base.metadata.create_all(get_engine())
        db = session_factory()()
        try:
            seed(db)
            disarm(db)
        finally:
            db.close()
        runtime.db_ok = True
    except Exception as exc:
        runtime.db_ok = False
        log.error("database startup failed: %s", type(exc).__name__)
    task = asyncio.create_task(market_loop(runtime))
    hist = asyncio.create_task(historical_loop(runtime))
    yield
    task.cancel()
    hist.cancel()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="HexTrade", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.cors_origin, "http://127.0.0.1:5999", "http://localhost:5999"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.include_router(trading_router)

    @app.exception_handler(OperationalError)
    async def database_unavailable(_request: Request, _exc: OperationalError):
        runtime = getattr(_request.app.state, "runtime", None)
        if runtime is not None:
            runtime.db_ok = False
        return JSONResponse(status_code=503, content={"detail": "Database is unavailable"})

    return app


app = create_app()

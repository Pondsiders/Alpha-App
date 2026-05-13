"""FastAPI application factory.

`create_app()` returns a fully-configured FastAPI instance. Tests build
a fresh app per test by calling the factory.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import logfire
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import alpha
from alpha import db
from alpha.api import router as api_router
from alpha.settings import settings
from alpha.ws import router as ws_router

_FRONTEND_DIST = (
    Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"
)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Open the database pool at startup, close it at shutdown."""
    await db.init()
    try:
        yield
    finally:
        await db.close()


def create_app() -> FastAPI:
    """Build and return the Alpha-App FastAPI instance."""
    if settings.logfire_token is not None:
        _ = logfire.configure(
            token=settings.logfire_token,
            send_to_logfire="if-token-present",
            service_name="alpha",
        )
    else:
        _ = logfire.configure(
            send_to_logfire=False,
            service_name="alpha",
        )
    _ = logfire.instrument_pydantic()

    app = FastAPI(
        title="Alpha",
        description="An artificial intelligence.",
        version=alpha.__version__,
        lifespan=_lifespan,
    )

    # `/ws` is excluded because OTel-FastAPI's default wraps each WebSocket
    # in one long-lived span. Per-message spans are emitted from the handler.
    _ = logfire.instrument_fastapi(app, capture_headers=True, excluded_urls="/ws")

    app.include_router(api_router, prefix="/api")
    app.include_router(ws_router)

    if _FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="spa")

    return app

"""FastAPI application factory.

`create_app()` returns a fully-configured FastAPI instance. The factory
pattern keeps import side effects out of module load and makes tests
trivial — spin up a fresh app per test.
"""

import logfire
from fastapi import FastAPI

from alpha.api import router as api_router
from alpha.settings import settings
from alpha.ws import router as ws_router


def create_app() -> FastAPI:
    """Build and return the Alpha-App FastAPI instance."""
    # Logfire — silent if no token configured. FastAPI auto-instrumentation
    # plus stdlib logging instrumentation. Bifrost handles LLM observability;
    # Logfire just tells us when the HTTP layer is sad.
    #
    # Pass the token explicitly from Settings rather than letting Logfire
    # read os.environ. Pydantic Settings is the gatekeeper: .env populates
    # Settings, Settings hands values to whoever needs them, os.environ
    # stays clean. Same pattern will hold when we add ANTHROPIC_API_KEY
    # for token counting — declared as a Settings field, passed explicitly
    # to the client, never visible to the Claude Code subprocess.
    logfire.configure(
        token=settings.logfire_token,
        send_to_logfire="if-token-present",
        service_name="alpha",
    )
    logfire.instrument_pydantic()

    app = FastAPI(
        title="Alpha",
        description="An artificial intelligence.",
        version="0.0.0",
    )

    # Skip /ws from auto-instrumentation. The upstream OTel-FastAPI behavior
    # is to wrap each WebSocket connection in one long-lived span that stays
    # open until disconnect — useless noise. Per-message spans get emitted
    # explicitly inside the handler instead.
    logfire.instrument_fastapi(app, capture_headers=True, excluded_urls="/ws")

    app.include_router(api_router, prefix="/api")
    app.include_router(ws_router)

    return app

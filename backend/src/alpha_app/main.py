"""Alpha backend — FastAPI application.

The mannequin's throat. The frog speaks through here. 🐸
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from alpha_sdk import read_soul
from alpha_app.chat import Chat, ChatState, Holster
from alpha_app.db import init_pool, close_pool
from alpha_app.routes.sessions import router as sessions_router
from alpha_app.routes.ws import router as ws_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("alpha")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """App lifespan — read soul, warm holster, clean shutdown."""
    # Startup
    log.info("Connecting to Postgres...")
    await init_pool()

    # Read the soul from the identity directory
    try:
        soul = read_soul()
        log.info("Soul loaded from $JE_NE_SAIS_QUOI")
    except (RuntimeError, FileNotFoundError) as e:
        log.warning("No soul found (%s) — running without system prompt", e)
        soul = ""

    holster = Holster(system_prompt=soul)
    app.state.holster = holster
    app.state.chats = {}  # dict[str, Chat]
    app.state.system_prompt = soul  # Stored for resurrection

    log.info("Warming holster (one in the chamber)...")
    await holster.warm()

    yield

    # Shutdown
    log.info("Shutting down...")
    for chat in list(app.state.chats.values()):
        if chat.state != ChatState.DEAD:
            await chat.reap()
    await holster.shutdown()
    await close_pool()
    log.info("Goodbye.")


app = FastAPI(
    title="Alpha",
    description="Alpha — the duck in the machine",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routes
app.include_router(sessions_router)
app.include_router(ws_router)


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    holster: Holster = app.state.holster
    chats: dict[str, Chat] = app.state.chats
    alive = [c for c in chats.values() if c.state != ChatState.DEAD]
    busy = [c for c in chats.values() if c.state == ChatState.BUSY]
    return {
        "status": "healthy",
        "holster_ready": holster.ready,
        "chats_total": len(chats),
        "chats_alive": len(alive),
        "chats_busy": len(busy),
    }


# -- Static file serving --------------------------------------------------
# Docker: built frontend lives at /app/frontend/dist
# Bare metal: built frontend lives at ../frontend/dist relative to backend/
_DOCKER_DIST = Path("/app/frontend/dist")
_LOCAL_DIST = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"
FRONTEND_DIR = _DOCKER_DIST if _DOCKER_DIST.is_dir() else _LOCAL_DIST

if FRONTEND_DIR.is_dir():
    # Serve Vite's hashed asset bundles
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="static-assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str) -> FileResponse:
        """Serve built frontend — SPA catch-all."""
        file_path = FRONTEND_DIR / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "18010"))
    uvicorn.run(app, host="0.0.0.0", port=port)

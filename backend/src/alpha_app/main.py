"""Alpha backend — FastAPI application.

The mannequin's throat. Haiku speaks through here.

One process. One client. Lazy initialization:
no client at startup, first chat request creates it.
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

from alpha_app.client import client
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
    """App lifespan — just cleanup on shutdown."""
    log.info("Starting up... (client will connect on first request)")
    yield
    log.info("Shutting down...")
    await client.shutdown()
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
async def health() -> dict[str, str | None]:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "client_connected": str(client.connected),
        "current_session": client.current_session_id[:8] + "..." if client.current_session_id else None,
    }


# ── Static file serving ──
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

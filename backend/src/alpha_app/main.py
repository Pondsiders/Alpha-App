"""Alpha backend — FastAPI application.

The mannequin's throat. The frog speaks through here. 🐸
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import logfire
from dotenv import load_dotenv

# Load .env from repo root (no-op if not present — Docker sets env directly)
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from alpha_app.constants import JE_NE_SAIS_QUOI
from alpha_app.memories import init_schema as init_cortex_schema, close as close_cortex
from alpha_app.chat import Chat, ConversationState
from alpha_app.db import init_pool, close_pool
from alpha_app.routes.ws import router as ws_router
from alpha_app.topics import TopicRegistry

# Observability — one place to look for everything.
# The valve: LOGFIRE_MIN_LEVEL controls what reaches the dashboard.
#   "info"  = normal operation (scheduler, jobs, errors)
#   "debug" = state transitions, lifecycle events
#   "trace" = every Claude subprocess event (the firehose)
_log_level = os.environ.get("LOGFIRE_MIN_LEVEL", "info")
logfire.configure(service_name="alpha-app", scrubbing=False, min_level=_log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """App lifespan — assemble system prompt, clean shutdown."""
    # Startup
    await init_pool()

    # Initialize Cortex schema (idempotent — like kanji)
    try:
        await init_cortex_schema()
    except Exception:
        pass  # Non-fatal — Cortex tools degrade gracefully without schema

    # Discover topics
    topics_dir = JE_NE_SAIS_QUOI / "topics"
    topic_registry = TopicRegistry(topics_dir)
    topic_registry.scan()

    app.state.chats = {}  # dict[str, Chat]
    app.state.connections = set()  # set[WebSocket] — all live WS connections (the switch)
    app.state.topic_registry = topic_registry  # Stored for MCP tool + enrobe

    # Scheduler — only when --with-scheduler is set (alpha-pi Docker, not Primer bare metal)
    scheduler = None
    if getattr(app.state, "_enable_scheduler", False):
        from alpha_app.scheduler import create_scheduler, sync_from_db
        scheduler = create_scheduler(app)
        scheduler.start()
        await sync_from_db(app)  # Populate APScheduler from app.jobs

    yield

    # Shutdown
    if scheduler:
        scheduler.shutdown(wait=False)
    for chat in list(app.state.chats.values()):
        if chat.state != ConversationState.COLD:
            await chat.reap()
    await close_cortex()
    await close_pool()


from alpha_app.clock import PSOResponse

app = FastAPI(
    title="Alpha",
    description="Alpha — the duck in the machine",
    version="0.1.0",
    lifespan=lifespan,
    default_response_class=PSOResponse,
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
app.include_router(ws_router)

from alpha_app.routes.schedule_api import router as schedule_router, solitude_router
app.include_router(schedule_router)
app.include_router(solitude_router)


@app.get("/api/demo/duck")
async def get_demo_duck():
    """Demo payload endpoint — mirror of the demo_duck MCP tool.

    Used to compare MCP tool return shape vs REST JSON response shape
    from Alpha's side. Same underlying function, two surfaces.
    """
    from alpha_app.demo import demo_duck
    return demo_duck()


@app.get("/api/theme")
async def get_theme():
    """Serve the identity's theme CSS from JE_NE_SAIS_QUOI/theme.css."""
    theme_path = JE_NE_SAIS_QUOI / "theme.css"
    if theme_path.is_file():
        return FileResponse(theme_path, media_type="text/css")
    from fastapi.responses import Response
    return Response(status_code=404, content="/* no theme.css in identity directory */")


@app.get("/api/threads")
async def get_threads():
    """Thread list for frontend-v2 sidebar."""
    from alpha_app.db import list_chats
    return await list_chats()


@app.get("/api/threads/{thread_id}/messages")
async def get_thread_messages(thread_id: str):
    """Recap: load all messages for a thread as ThreadMessageLike[] for assistant-ui.

    Converts our internal format to assistant-ui's ThreadMessageLike format:
      - User content[] → content[] (same shape)
      - Assistant thinking → reasoning type
      - Assistant tool-call → tool-{name} type
    Note: ThreadMessageLike uses 'content', not 'parts' (UIMessage uses 'parts').
    """
    from alpha_app.db import load_messages

    rows = await load_messages(thread_id)
    ui_messages = []

    for row in rows:
        role = row["role"]
        data = row.get("data", {})
        msg_id = data.get("id", f"msg-{len(ui_messages)}")
        source = data.get("source", "human")

        # Skip suggest/narrator messages — they're internal
        if source in ("suggest",):
            continue

        if role == "user":
            # User: content[] → parts[] (same shape, just rename the key)
            parts = []
            for block in data.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append({"type": "text", "text": block.get("text", "")})
                # Skip non-text blocks (images, etc.) for now
            if not parts:
                continue
            ui_messages.append({
                "id": msg_id,
                "role": "user",
                "content": parts,
            })

        elif role == "assistant":
            # Assistant: parts[] with type mapping
            parts = []
            for part in data.get("parts", []):
                ptype = part.get("type")
                if ptype == "text":
                    parts.append({"type": "text", "text": part.get("text", "")})
                elif ptype == "thinking":
                    parts.append({"type": "reasoning", "text": part.get("thinking", "")})
                elif ptype == "tool-call":
                    tool_name = part.get("toolName", "unknown")
                    parts.append({
                        "type": f"tool-{tool_name}",
                        "toolCallId": part.get("toolCallId", ""),
                        "state": "output-available",
                        "input": part.get("args", {}),
                        "output": part.get("result", ""),
                    })
            if not parts:
                continue
            ui_messages.append({
                "id": msg_id,
                "role": "assistant",
                "content": parts,
            })

    return ui_messages


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    chats: dict[str, Chat] = app.state.chats
    alive = [c for c in chats.values() if c.state != ConversationState.COLD]
    busy = [c for c in chats.values() if c.state == ConversationState.RESPONDING]
    return {
        "status": "healthy",
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


def _rebuild_frontend_if_stale(frontend_dir: Path) -> None:
    """Run `npm run build` if any frontend/src file is newer than dist/index.html.

    This is only called on bare-metal (not Docker). The timestamp check is the
    only cost on a clean restart — subprocess is not spawned unless needed.
    """
    import subprocess

    src_dir = frontend_dir / "src"
    dist_index = frontend_dir / "dist" / "index.html"

    if not src_dir.is_dir():
        return  # Nothing to build

    if dist_index.exists():
        dist_mtime = dist_index.stat().st_mtime
        stale = any(
            p.stat().st_mtime > dist_mtime
            for p in src_dir.rglob("*")
            if p.is_file()
        )
        if not stale:
            return

    print("Frontend source has changed — rebuilding…", flush=True)
    result = subprocess.run(["npm", "run", "build"], cwd=frontend_dir, check=False)
    if result.returncode != 0:
        print(f"Warning: frontend build exited with code {result.returncode}", flush=True)


def run() -> None:
    """Entry point for `uv run alpha` (bare metal deployment)."""
    import argparse
    import uvicorn

    from alpha_app.constants import PORT

    parser = argparse.ArgumentParser(description="Alpha backend server")
    parser.add_argument("--port", type=int, default=PORT, help=f"Port to serve on (default: {PORT})")
    parser.add_argument("--with-scheduler", action="store_true",
                        help="Enable APScheduler for Solitude, capsules, and other background jobs")
    args = parser.parse_args()

    # Signal the lifespan to start the scheduler
    if args.with_scheduler:
        app.state._enable_scheduler = True

    # Rebuild frontend if source is newer than the bundle (bare-metal only).
    if not _DOCKER_DIST.is_dir():
        _rebuild_frontend_if_stale(_LOCAL_DIST.parent)

    # Plain HTTP. Always. Tailscale Serve handles TLS termination for the
    # production container; Vite dev server handles HTTPS for local dev.
    # Nothing downstream needs uvicorn to terminate HTTPS.
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    run()

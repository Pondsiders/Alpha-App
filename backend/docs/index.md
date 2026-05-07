# Alpha Backend

The Python service that powers Alpha — a FastAPI app speaking a small
WebSocket protocol to the frontend, plus an HTTP `/api/health` endpoint.

These docs are generated from source. The reference pages reflect
whatever's currently in `src/alpha`; if a class or function isn't here,
it doesn't exist in the code.

## Quick start

```bash
cd backend
uv sync
uv run alpha
```

The app stands up on `http://0.0.0.0:8000` by default. `host`, `port`,
and `dev` (uvicorn auto-reload) come from `.env` via `pydantic-settings`.

## Reading these docs

```bash
cd backend
uv sync --group docs
uv run mkdocs serve
```

Open <http://127.0.0.1:8000> for live-reloading docs while you read.
The site is never built into static HTML and never deployed — it runs
locally as long as you want it, and goes away when you stop the server.

## Reference

The Reference section in the sidebar mirrors the package layout under
`src/alpha`. Each module gets its own page; classes and functions show
signatures with type annotations, source links, and docstrings.

The reference is built fresh from source on every server reload. Add a
new module → it appears in the tree on next save.

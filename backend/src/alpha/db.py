"""Database connection pool — one asyncpg pool, owned by the process.

`init()` and `close()` are wired into the FastAPI lifespan in `app.py`.
Persistence modules (`chats.py`, etc.) call `get()` to reach the pool:

    async with db.get().acquire() as conn:
        await conn.execute(...)

The pool is module-level state so any module that needs Postgres can
import `get` and use it without threading the pool through call sites.
"""

import asyncpg
from asyncpg.pool import Pool

from alpha.settings import settings

_pool: Pool | None = None


async def init() -> None:
    """Open the pool. Call once at app startup."""
    global _pool
    _pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=2,
        max_size=10,
    )


async def close() -> None:
    """Close the pool. Call once at app shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get() -> Pool:
    """Return the live pool. Raises if `init()` hasn't been called."""
    if _pool is None:
        msg = "db pool not initialized; call db.init() first"
        raise RuntimeError(msg)
    return _pool

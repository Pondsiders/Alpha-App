"""Database module — asyncpg connection pool for Postgres.

The oak in the center of Pondside. All chat persistence lives here.
Replaces Redis for Alpha-App.

All tables live in the `app` schema and are referenced explicitly
(app.chats, not just chats). No search_path tricks.

Usage:
    # In lifespan:
    await init_pool()
    yield
    await close_pool()

    # In handlers:
    pool = get_pool()
    row = await pool.fetchrow("SELECT ... FROM app.chats ...", arg)
"""

import json
import os

import asyncpg

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Configure each connection in the pool.

    Registers JSONB codec so Python dicts go in and come out automatically.
    """
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def init_pool() -> None:
    """Initialize the connection pool. Call once at startup."""
    global _pool
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set — cannot connect to Postgres")

    _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10, init=_init_connection)


async def close_pool() -> None:
    """Close the connection pool. Call once at shutdown."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    """Get the connection pool. Raises if not initialized."""
    if _pool is None:
        raise RuntimeError("Postgres pool not initialized — call init_pool() first")
    return _pool

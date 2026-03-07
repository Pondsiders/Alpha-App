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
from datetime import datetime, timezone

import asyncpg

from alpha_app.chat import Chat

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


# -- Chat persistence ---------------------------------------------------------


async def persist_chat(chat: Chat) -> None:
    """Persist chat metadata to Postgres. Upsert by chat ID."""
    try:
        pool = get_pool()
        updated = datetime.fromtimestamp(chat.updated_at, tz=timezone.utc)
        await pool.execute(
            """
            INSERT INTO app.chats (id, updated_at, data)
            VALUES ($1, $2, $3)
            ON CONFLICT (id) DO UPDATE
                SET updated_at = EXCLUDED.updated_at,
                    data = EXCLUDED.data
            """,
            chat.id,
            updated,
            chat.to_data(),
        )
    except Exception:
        pass  # Non-fatal


async def list_chats() -> list[dict]:
    """Load chat list from Postgres for sidebar hydration."""
    try:
        pool = get_pool()
        rows = await pool.fetch(
            """
            SELECT id, updated_at, data
            FROM app.chats
            ORDER BY updated_at DESC
            LIMIT 100
            """
        )
        result = []
        for row in rows:
            data = row["data"]
            result.append({
                "chatId": row["id"],
                "title": data.get("title", ""),
                "state": "dead",
                "updatedAt": row["updated_at"].timestamp(),
                "sessionUuid": data.get("session_uuid", ""),
                "tokenCount": data.get("token_count", 0) or 0,
                "contextWindow": data.get("context_window", 0) or 200_000,
            })
        return result
    except Exception:
        return []


async def load_chat(chat_id: str) -> Chat | None:
    """Load a chat's metadata from Postgres. Returns a DEAD Chat, or None."""
    try:
        pool = get_pool()
        row = await pool.fetchrow(
            "SELECT id, updated_at, data FROM app.chats WHERE id = $1",
            chat_id,
        )
        if row:
            return Chat.from_db(
                chat_id=row["id"],
                updated_at=row["updated_at"].timestamp(),
                data=row["data"],
            )
        return None
    except Exception:
        return None

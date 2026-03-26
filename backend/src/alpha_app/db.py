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
from alpha_app.constants import CONTEXT_WINDOW

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

    # Idempotent migration: add seq column to app.events if not already present.
    # seq captures true broadcast order and is safe to ORDER BY (unlike BIGSERIAL
    # id, which can be assigned out of order across pool connections).
    # Note: any pre-migration rows with seq = NULL will sort LAST in PostgreSQL's
    # default ASC ordering (NULLs last), after all correctly-sequenced new rows.
    async with _pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE app.events ADD COLUMN IF NOT EXISTS seq INTEGER"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_chat_seq"
            " ON app.events (chat_id, seq)"
        )

        # Message-level storage: the "gimme the fucking chat" table.
        # Each row is one complete UserMessage or AssistantMessage as JSONB.
        # Replaces event replay with a single SELECT for chat loading.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS app.messages (
                chat_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                role TEXT NOT NULL,
                data JSONB NOT NULL,
                PRIMARY KEY (chat_id, ordinal)
            )
        """)


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
                "createdAt": data.get("created_at", 0) or row["updated_at"].timestamp(),
                "sessionUuid": data.get("session_uuid", ""),
                "tokenCount": data.get("token_count", 0) or 0,
                "contextWindow": data.get("context_window", 0) or CONTEXT_WINDOW,
            })
        return result
    except Exception:
        return []


# -- Event store --------------------------------------------------------------


async def store_event(chat_id: str, event: dict, seq: int) -> None:
    """Append an event to the event stream for a chat.

    seq is a monotonically increasing counter assigned at broadcast time
    (before any await), capturing true event order regardless of which pool
    connection commits first.
    """
    try:
        pool = get_pool()
        await pool.execute(
            "INSERT INTO app.events (chat_id, event, seq) VALUES ($1, $2, $3)",
            chat_id,
            event,
            seq,
        )
    except Exception:
        pass  # Non-fatal — don't break streaming if the INSERT fails


async def replay_events(chat_id: str) -> list[dict]:
    """Load all events for a chat, ordered by seq. For UI replay."""
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT event FROM app.events WHERE chat_id = $1 ORDER BY seq",
        chat_id,
    )
    return [row["event"] for row in rows]


# -- Message storage (the "gimme the fucking chat" table) --------------------


async def store_message(chat_id: str, ordinal: int, role: str, data: dict) -> None:
    """Store a complete UserMessage or AssistantMessage.

    Dual-write: called alongside store_event during the transition period.
    Once replay is replaced with join-chat, store_event goes away.
    """
    try:
        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO app.messages (chat_id, ordinal, role, data)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (chat_id, ordinal) DO UPDATE SET data = EXCLUDED.data
            """,
            chat_id,
            ordinal,
            role,
            data,
        )
    except Exception:
        pass  # Non-fatal


async def load_messages(chat_id: str) -> list[dict]:
    """Load all messages for a chat, ordered. For join-chat.

    Returns list of {"role": "user"|"assistant", "data": {...}} dicts.
    """
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT role, data FROM app.messages WHERE chat_id = $1 ORDER BY ordinal",
        chat_id,
    )
    return [{"role": row["role"], "data": row["data"]} for row in rows]


async def next_message_ordinal(chat_id: str) -> int:
    """Get the next ordinal for a message in a chat."""
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT COALESCE(MAX(ordinal), -1) + 1 AS next_ord FROM app.messages WHERE chat_id = $1",
        chat_id,
    )
    return row["next_ord"]


# -- Chat loading -------------------------------------------------------------


async def load_chat(chat_id: str) -> Chat | None:
    """Load a chat's metadata AND messages from Postgres. Returns a DEAD Chat, or None.

    Messages are loaded eagerly so that Chat.messages[] matches Postgres
    from the start. This is critical for flush() — ordinals are array
    indices, so an empty messages[] would overwrite existing rows at
    ordinal 0, 1, etc.
    """
    try:
        pool = get_pool()
        row = await pool.fetchrow(
            "SELECT id, updated_at, data FROM app.chats WHERE id = $1",
            chat_id,
        )
        if row:
            chat = Chat.from_db(
                chat_id=row["id"],
                updated_at=row["updated_at"].timestamp(),
                data=row["data"],
            )
            await chat.load_messages()
            return chat
        return None
    except Exception:
        return None

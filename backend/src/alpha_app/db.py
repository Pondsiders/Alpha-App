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
from typing import Any

import asyncpg
import logfire

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

    async with _pool.acquire() as conn:
        # Bootstrap schema + core tables. All idempotent (IF NOT EXISTS) so
        # this is safe to run against existing production databases AND makes
        # a fresh database self-sufficient — just create the empty database,
        # run the app, and it sets itself up.
        await conn.execute("CREATE SCHEMA IF NOT EXISTS app")

        # Chat metadata — one row per chat, JSONB carries everything flexible.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS app.chats (
                id TEXT PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                data JSONB NOT NULL DEFAULT '{}'
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chats_updated_at"
            " ON app.chats (updated_at DESC)"
        )

        # Raw event log — used by the legacy replay protocol.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS app.events (
                id BIGSERIAL PRIMARY KEY,
                chat_id TEXT NOT NULL,
                ts TIMESTAMPTZ NOT NULL DEFAULT now(),
                event JSONB NOT NULL,
                seq INTEGER
            )
        """)

        # Idempotent migration: add seq column to app.events if not already
        # present (for databases created before the column was added). seq
        # captures true broadcast order and is safe to ORDER BY (unlike
        # BIGSERIAL id, which can be assigned out of order across pool
        # connections). Note: any pre-migration rows with seq = NULL will
        # sort LAST in PostgreSQL's default ASC ordering (NULLs last), after
        # all correctly-sequenced new rows.
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

        # App state — single JSONB row for all ephemeral app state.
        # No per-value tables. Future state is a new key, not a new migration.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS app.state (
                id INTEGER PRIMARY KEY DEFAULT 1,
                data JSONB NOT NULL DEFAULT '{}',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT state_single_row CHECK (id = 1)
            )
        """)
        # Ensure the single row exists
        await conn.execute("""
            INSERT INTO app.state (id, data) VALUES (1, '{}')
            ON CONFLICT (id) DO NOTHING
        """)

        # Reflection flags — the "highlighter" to store's "notepad".
        # Alpha drops a silent bookmark mid-turn via flag_for_reflection(note);
        # the next scheduled post-turn reminder surfaces unclaimed flags in
        # its body. Decouples noticing from reflecting.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS app.reflection_flags (
                id BIGSERIAL PRIMARY KEY,
                chat_id TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                note TEXT NOT NULL,
                claimed BOOLEAN NOT NULL DEFAULT FALSE
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reflection_flags_chat_unclaimed"
            " ON app.reflection_flags (chat_id, claimed)"
        )

        # Capsules — day/night continuity letters.
        # Written by the Dusk ghost (day) and Solitude (night).
        # Read by Dawn to build the next day's system prompt.
        await conn.execute("CREATE SCHEMA IF NOT EXISTS cortex")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS cortex.capsules (
                id BIGSERIAL PRIMARY KEY,
                kind TEXT NOT NULL CHECK (kind IN ('day', 'night')),
                chat_id TEXT,
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_capsules_kind_created"
            " ON cortex.capsules (kind, created_at DESC)"
        )

        # Job persistence — our own table, plain JSON, no pickle.
        # APScheduler is pure in-memory; this is the source of truth.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS app.jobs (
                id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                fire_at TIMESTAMPTZ NOT NULL,
                kwargs JSONB DEFAULT '{}'
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
        created = datetime.fromtimestamp(chat.created_at, tz=timezone.utc)
        await pool.execute(
            """
            INSERT INTO app.chats (id, created_at, updated_at, data)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id) DO UPDATE
                SET updated_at = EXCLUDED.updated_at,
                    data = EXCLUDED.data
            """,
            chat.id,
            created,
            updated,
            chat.to_data(),
        )
    except Exception as e:
        logfire.error(
            "persist_chat FAILED: {error} chat={chat_id}",
            error=str(e),
            chat_id=chat.id,
            exc_info=True,
        )


async def list_chats() -> list[dict]:
    """Load chat list from Postgres for sidebar hydration."""
    try:
        pool = get_pool()
        rows = await pool.fetch(
            """
            SELECT id, created_at, updated_at, data
            FROM app.chats
            ORDER BY created_at DESC
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
                "createdAt": row["created_at"].timestamp(),
                "sessionUuid": data.get("session_uuid", ""),
                "tokenCount": data.get("token_count", 0) or 0,
                "contextWindow": data.get("context_window", 0) or CONTEXT_WINDOW,
            })
        return result
    except Exception as e:
        logfire.error("list_chats FAILED: {error}", error=str(e), exc_info=True)
        return []


# -- Reflection flags ---------------------------------------------------------


async def insert_reflection_flag(chat_id: str, note: str) -> int | None:
    """Insert a reflection flag. Returns the new flag ID, or None on error."""
    try:
        pool = get_pool()
        row = await pool.fetchrow(
            "INSERT INTO app.reflection_flags (chat_id, note)"
            " VALUES ($1, $2) RETURNING id",
            chat_id,
            note,
        )
        return row["id"] if row else None
    except Exception as e:
        logfire.error(
            "insert_reflection_flag FAILED: {error} chat={chat_id}",
            error=str(e),
            chat_id=chat_id,
            exc_info=True,
        )
        return None


async def fetch_unclaimed_flags(chat_id: str) -> list[dict]:
    """Fetch all unclaimed reflection flags for a chat, oldest first."""
    try:
        pool = get_pool()
        rows = await pool.fetch(
            "SELECT id, note, created_at FROM app.reflection_flags"
            " WHERE chat_id = $1 AND claimed = FALSE"
            " ORDER BY created_at ASC",
            chat_id,
        )
        return [{"id": r["id"], "note": r["note"], "created_at": r["created_at"]} for r in rows]
    except Exception as e:
        logfire.error(
            "fetch_unclaimed_flags FAILED: {error} chat={chat_id}",
            error=str(e),
            chat_id=chat_id,
            exc_info=True,
        )
        return []


async def claim_flags(flag_ids: list[int]) -> None:
    """Mark flags as claimed. Idempotent — already-claimed rows stay claimed."""
    if not flag_ids:
        return
    try:
        pool = get_pool()
        await pool.execute(
            "UPDATE app.reflection_flags SET claimed = TRUE WHERE id = ANY($1::bigint[])",
            flag_ids,
        )
    except Exception as e:
        logfire.error(
            "claim_flags FAILED: {error} ids={ids}",
            error=str(e),
            ids=flag_ids,
            exc_info=True,
        )


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
    except Exception as e:
        logfire.error(
            "store_event FAILED: {error} chat={chat_id}",
            error=str(e),
            chat_id=chat_id,
            exc_info=True,
        )


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
    except Exception as e:
        logfire.error(
            "store_message FAILED: {error} chat={chat_id} ordinal={ordinal}",
            error=str(e),
            chat_id=chat_id,
            ordinal=ordinal,
            exc_info=True,
        )


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
            "SELECT id, created_at, updated_at, data FROM app.chats WHERE id = $1",
            chat_id,
        )
        if row:
            chat = Chat.from_db(
                chat_id=row["id"],
                created_at=row["created_at"].timestamp(),
                updated_at=row["updated_at"].timestamp(),
                data=row["data"],
            )
            await chat.load_messages()
            return chat
        return None
    except Exception as e:
        logfire.error("load_chat FAILED: {error} chat={chat_id}", error=str(e), chat_id=chat_id, exc_info=True)
        return None


# -- App state (single JSONB row) -------------------------------------------


async def get_state(key: str) -> Any:
    """Read a value from app.state."""
    pool = get_pool()
    row = await pool.fetchval("SELECT data->>$1 FROM app.state WHERE id = 1", key)
    return json.loads(row) if row else None


async def set_state(key: str, value: Any) -> None:
    """Write a value to app.state (merge, don't replace)."""
    pool = get_pool()
    await pool.execute("""
        INSERT INTO app.state (id, data) VALUES (1, $1::jsonb)
        ON CONFLICT (id) DO UPDATE
            SET data = app.state.data || $1::jsonb, updated_at = now()
    """, json.dumps({key: value}))


async def clear_state(key: str) -> None:
    """Remove a key from app.state."""
    pool = get_pool()
    await pool.execute(
        "UPDATE app.state SET data = data - $1, updated_at = now() WHERE id = 1", key
    )

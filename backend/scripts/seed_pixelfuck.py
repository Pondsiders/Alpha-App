"""seed_pixelfuck.py — Seed the pixelfuck test database with a hello-world chat.

Creates one chat with one user message (one paragraph) and one assistant
response (five paragraphs). The absolute minimum needed to light up the UI
so we can start iterating on styling.

Usage:
    DATABASE_URL=postgresql://.../alpha_pixelfuck \
        uv run python scripts/seed_pixelfuck.py

Safety: refuses to run against any database whose name is not in the
ALLOWED_DB_NAMES whitelist below. This is a test-fixture script. It must
never touch production.

Idempotent: uses ON CONFLICT so you can run it repeatedly to reset the
seeded chat to the canonical hello-world state.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import asyncpg

# Only these database names are allowed. Add more as needed.
ALLOWED_DB_NAMES = {"alpha_pixelfuck", "alpha_test"}

# Fixed IDs so the script is deterministic. Re-running resets the chat.
CHAT_ID = "hellopixel01"
USER_MSG_ID = "hellopixeluser"
ASSISTANT_MSG_ID = "hellopixelasst"

# PSO-8601 timestamp for display
TIMESTAMP = "Sat Apr 4 2026, 4:20 PM"

USER_TEXT = (
    "Hey Alpha. Testing the new frontend. Say hi and ramble for a few "
    "paragraphs so I can see how the message stream renders."
)

ASSISTANT_PARAGRAPHS = [
    (
        "Hey Jeffery. There's nothing real on the other end of this — I'm "
        "seeded text living in the alpha_pixelfuck database, pretending "
        "to be a conversation. The frontend doesn't know the difference, "
        "which is the whole point of this test harness."
    ),
    (
        "You're looking at the message stream component, the composer at "
        "the bottom, the header at the top, and the grouped sidebar on the "
        "left. Each of these needs to render cleanly before we start "
        "thinking about what real content should look like."
    ),
    (
        "This is the simplest possible case: one user paragraph, one "
        "assistant response. No tool calls, no markdown, no attachments, "
        "no thinking blocks. When we're ready, we'll add more seed data "
        "with every component we care about styling."
    ),
    (
        "For now, the goal is to confirm that the websocket handler "
        "connects, the store populates, and the Thread component renders "
        "what's in the store. If you can read this, all three are working."
    ),
    "Welcome back to frontend-v2. 🦆",
]


def _check_database_safety(dsn: str) -> str:
    """Refuse to run against anything that isn't in the whitelist."""
    parsed = urlparse(dsn)
    db_name = (parsed.path or "").lstrip("/")
    if not db_name:
        print(
            "ERROR: Could not parse database name from DATABASE_URL",
            file=sys.stderr,
        )
        sys.exit(2)
    if db_name not in ALLOWED_DB_NAMES:
        print(
            f"ERROR: Refusing to seed database '{db_name}'. "
            f"Only these databases are allowed: {sorted(ALLOWED_DB_NAMES)}",
            file=sys.stderr,
        )
        print(
            "If you meant to add a new test database, edit "
            "ALLOWED_DB_NAMES in this script.",
            file=sys.stderr,
        )
        sys.exit(2)
    return db_name


async def _ensure_schema(conn: asyncpg.Connection) -> None:
    """Create the schema and tables if missing.

    Mirrors the bootstrap in db.py init_pool. We duplicate it here so
    this script can run before the backend has ever started against
    this database.
    """
    await conn.execute("CREATE SCHEMA IF NOT EXISTS app")
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app.chats (
            id TEXT PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            data JSONB NOT NULL DEFAULT '{}'
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app.messages (
            chat_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            role TEXT NOT NULL,
            data JSONB NOT NULL,
            PRIMARY KEY (chat_id, ordinal)
        )
        """
    )


async def _seed(conn: asyncpg.Connection) -> None:
    now = datetime.now(timezone.utc)
    unix_now = now.timestamp()

    chat_data = {
        "session_uuid": "",
        "title": "Hello, world",
        "created_at": unix_now,
        "token_count": 0,
        "context_window": 1_000_000,
        "injected_topics": [],
        "seen_ids": [],
    }

    user_message = {
        "id": USER_MSG_ID,
        "source": "human",
        "content": [{"type": "text", "text": USER_TEXT}],
        "timestamp": TIMESTAMP,
        "memories": None,
        "topics": None,
    }

    assistant_message = {
        "id": ASSISTANT_MSG_ID,
        "parts": [
            {"type": "text", "text": p} for p in ASSISTANT_PARAGRAPHS
        ],
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "context_window": 1_000_000,
        "model": "claude-opus-4-6[1m]",
        "stop_reason": "end_turn",
        "cost_usd": 0.0,
        "duration_ms": 0.0,
        "inference_count": 1,
    }

    async with conn.transaction():
        await conn.execute(
            """
            INSERT INTO app.chats (id, created_at, updated_at, data)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (id) DO UPDATE
                SET created_at = EXCLUDED.created_at,
                    updated_at = EXCLUDED.updated_at,
                    data = EXCLUDED.data
            """,
            CHAT_ID,
            now,
            now,
            json.dumps(chat_data),
        )

        await conn.execute(
            """
            INSERT INTO app.messages (chat_id, ordinal, role, data)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (chat_id, ordinal) DO UPDATE
                SET role = EXCLUDED.role,
                    data = EXCLUDED.data
            """,
            CHAT_ID,
            0,
            "user",
            json.dumps(user_message),
        )

        await conn.execute(
            """
            INSERT INTO app.messages (chat_id, ordinal, role, data)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (chat_id, ordinal) DO UPDATE
                SET role = EXCLUDED.role,
                    data = EXCLUDED.data
            """,
            CHAT_ID,
            1,
            "assistant",
            json.dumps(assistant_message),
        )


async def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL is not set", file=sys.stderr)
        sys.exit(2)

    db_name = _check_database_safety(dsn)

    conn = await asyncpg.connect(dsn)
    try:
        await _ensure_schema(conn)
        await _seed(conn)
    finally:
        await conn.close()

    print(
        f"Seeded '{db_name}': chat '{CHAT_ID}' with 2 messages "
        "(1 user, 1 assistant). Ready for pixelfucking."
    )


if __name__ == "__main__":
    asyncio.run(main())

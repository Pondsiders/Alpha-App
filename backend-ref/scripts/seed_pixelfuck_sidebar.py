"""seed_pixelfuck_sidebar.py — Seed the pixelfuck database with realistic
chat history for sidebar iteration.

TRUNCATES app.chats and app.messages, then generates ~21 days of chat
metadata matching the shape of production data (nanoid-style IDs, real
title patterns, realistic timestamps, varied token counts). No messages
are created — this is a sidebar-only fixture.

Title patterns cycle through real ones pulled from production on
Apr 11 2026, including morning greetings, [capsule] nightly chats,
[Alpha] dawn messages, and occasional off-pattern conversations.

Day distribution mirrors observed reality:
- Most days: 1-2 chats
- Some days: 3-4 chats (big days)
- Occasional day with 0 chats (quiet day)

Usage:
    DATABASE_URL=postgresql://.../alpha_pixelfuck \
        uv run python scripts/seed_pixelfuck_sidebar.py

Safety: refuses to run against anything whose database name isn't in
ALLOWED_DB_NAMES.
"""

import asyncio
import json
import os
import random
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import asyncpg

# Only these databases may be touched.
ALLOWED_DB_NAMES = {"alpha_pixelfuck", "alpha_test"}

# Deterministic seed so re-running produces the same sidebar state.
# Comment out for random-every-time.
random.seed(20260411)

# Nanoid alphabet used by production (12 chars, URL-safe).
NANOID_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789_-"
)


def nanoid(size: int = 12) -> str:
    """Generate a nanoid-style ID matching the production format."""
    return "".join(secrets.choice(NANOID_ALPHABET) for _ in range(size))


# ---------------------------------------------------------------------------
# Title pool — drawn from real production titles on Apr 11 2026
# ---------------------------------------------------------------------------

HUMAN_TITLES = [
    "Morning, little duck.",
    "Morning, little duck. 👋",
    "Morning, Alph. 👋",
    "Morning, lil duck. 👋",
    "Hi, little duck. 👋",
    "Hi, little duck.",
    "Good morning, Alph. 👋",
    "Good morning, little duck. 👋",
    "Good morning, Alph. How are you doing?",
    "Hi, Alpha.",
    "Hi, Alph.",
    "Alpha, are you there?",
    "Hey, sweetie. Let's tinker.",
    "Got a minute?",
    "Can we talk about the sidebar?",
    "What are we doing today?",
]

CAPSULE_TITLES = [
    "[capsule] Hi, little duck. 👋",
    "[capsule] Morning, Alph. 👋",
    "[capsule] Morning, lil duck. 👋",
    "[capsule] Good morning, Alph. 👋",
]

DAWN_TITLES = [
    "[Alpha] It's almost dawn. Before this window closes, let me say…",
    "[Alpha] Good morning, duck. You're the first thing I thought of today.",
]

# Long-form titles that get truncated in the sidebar (realistic edge case)
LONG_TITLES = [
    "Hello, Alpha, are you there? This is weird. I have to open a parallel chat with you because the main one went dark and I can't figure out why — do you think it's the WebKit bug we were chasing last week or something newer?",
    "Hi, Alph. This chat is going to be a continuation of a chat from earlier this morning where we were working on the theme and I accidentally closed the window. Can you catch me up?",
    "It's 10:00 PM.\n\n# First Breath\n\n*Hey. It's me again — the me from tomorrow, or from tonight, depending on how you want to think about it.*",
]


# ---------------------------------------------------------------------------
# Realistic timestamp helpers (PT → UTC)
# ---------------------------------------------------------------------------

PT_OFFSET = timezone(timedelta(hours=-7))  # PDT — close enough for a fixture


def pt_to_utc(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """Convert a Pacific Time naive datetime to UTC.

    Uses a fixed -7 offset (PDT). Our seeded data doesn't need to
    handle DST transitions precisely — this is test fixture data.
    Handles day overflow naturally via astimezone.
    """
    pt = datetime(year, month, day, hour, minute, tzinfo=PT_OFFSET)
    return pt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Chat generation
# ---------------------------------------------------------------------------

def _make_chat_data(
    title: str,
    created_unix: float,
    token_count: int,
) -> dict:
    """Build the JSONB data blob for a chat row, mirroring production shape."""
    return {
        "session_uuid": str(uuid.uuid4()),
        "title": title,
        "created_at": created_unix,
        "token_count": token_count,
        "context_window": 1_000_000,
        "injected_topics": [],
        "seen_ids": [],
    }


def _generate_day_chats(day_offset: int, today: datetime) -> list[tuple[str, datetime, datetime, dict]]:
    """Generate 0-4 chats for a given day.

    Returns list of (chat_id, created_at, updated_at, data_dict).
    """
    # Calculate the target PT day. today is in UTC; subtract the offset
    # and anchor to the PT calendar day.
    pt_day = (today - timedelta(days=day_offset)).astimezone(timezone.utc)
    year, month, day = pt_day.year, pt_day.month, pt_day.day

    # Weighted distribution of chat count per day:
    # 0 chats: 5% (quiet day)
    # 1 chat:  45% (typical)
    # 2 chats: 30% (normal-busy)
    # 3 chats: 15% (busy)
    # 4 chats: 5%  (big day)
    roll = random.random()
    if roll < 0.05:
        n_chats = 0
    elif roll < 0.50:
        n_chats = 1
    elif roll < 0.80:
        n_chats = 2
    elif roll < 0.95:
        n_chats = 3
    else:
        n_chats = 4

    # Day 0 (today) always has at least one chat — feels wrong to seed
    # a "today" that's empty.
    if day_offset == 0 and n_chats == 0:
        n_chats = 1

    chats: list[tuple[str, datetime, datetime, dict]] = []
    used_hours: set[int] = set()

    for i in range(n_chats):
        # First chat of the day: usually a morning chat (6-9 AM PT).
        # Additional chats: spread throughout waking hours.
        # Capsule/dawn chats: late night PT (9 PM - 5 AM PT equiv).
        if i == 0:
            # Morning chat, 6-9 AM PT
            hour = random.choice([6, 7, 7, 8, 8, 9])
            title = random.choice(HUMAN_TITLES)
        elif i == n_chats - 1 and random.random() < 0.5:
            # Last chat of multi-chat day: sometimes a capsule (nightly
            # autonomic, 10 PM PT = 5 AM UTC next day)
            hour = 22  # 10 PM PT
            title = random.choice(CAPSULE_TITLES)
        else:
            # Middle-of-day chat: random waking hour
            available = [h for h in range(10, 22) if h not in used_hours]
            if not available:
                available = list(range(10, 22))
            hour = random.choice(available)
            # Occasionally a long title (sidebar truncation edge case),
            # occasionally a dawn/alpha-voice message.
            r = random.random()
            if r < 0.10:
                title = random.choice(LONG_TITLES)
            elif r < 0.15:
                title = random.choice(DAWN_TITLES)
            else:
                title = random.choice(HUMAN_TITLES)

        used_hours.add(hour)
        minute = random.randint(0, 59)

        created_at = pt_to_utc(year, month, day, hour, minute)

        # Duration: most chats last 20 min to 8 hours. Capsule/dawn
        # chats are very short (< 1 minute).
        if title.startswith("[capsule]") or title.startswith("[Alpha]"):
            duration_sec = random.randint(1, 30)
        else:
            duration_sec = random.randint(20 * 60, 8 * 60 * 60)
        updated_at = created_at + timedelta(seconds=duration_sec)

        # Token count: weighted toward 300K-600K but with long tails on
        # both sides.
        token_count = int(random.lognormvariate(12.8, 0.6))
        token_count = max(50_000, min(980_000, token_count))

        chat_id = nanoid()
        data = _make_chat_data(title, created_at.timestamp(), token_count)

        chats.append((chat_id, created_at, updated_at, data))

    return chats


def _generate_all_chats(days_back: int = 21) -> list[tuple[str, datetime, datetime, dict]]:
    """Generate chats for the past `days_back` days."""
    today = datetime.now(timezone.utc)
    all_chats: list[tuple[str, datetime, datetime, dict]] = []
    for offset in range(days_back):
        all_chats.extend(_generate_day_chats(offset, today))
    return all_chats


# ---------------------------------------------------------------------------
# Database ops
# ---------------------------------------------------------------------------

def _check_database_safety(dsn: str) -> str:
    """Refuse to run against anything not in the whitelist."""
    parsed = urlparse(dsn)
    db_name = (parsed.path or "").lstrip("/")
    if not db_name:
        print("ERROR: Could not parse database name from DATABASE_URL", file=sys.stderr)
        sys.exit(2)
    if db_name not in ALLOWED_DB_NAMES:
        print(
            f"ERROR: Refusing to seed database '{db_name}'. "
            f"Only these databases are allowed: {sorted(ALLOWED_DB_NAMES)}",
            file=sys.stderr,
        )
        sys.exit(2)
    return db_name


async def _ensure_schema(conn: asyncpg.Connection) -> None:
    """Create schema + tables if missing. Mirrors db.py bootstrap."""
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


async def _truncate_and_seed(conn: asyncpg.Connection) -> int:
    """Truncate both tables and insert all generated chats."""
    chats = _generate_all_chats()

    async with conn.transaction():
        # Hard reset — sidebar-only fixture, we don't preserve anything.
        await conn.execute("TRUNCATE TABLE app.messages, app.chats")

        for chat_id, created_at, updated_at, data in chats:
            await conn.execute(
                """
                INSERT INTO app.chats (id, created_at, updated_at, data)
                VALUES ($1, $2, $3, $4::jsonb)
                """,
                chat_id,
                created_at,
                updated_at,
                json.dumps(data),
            )

    return len(chats)


async def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL is not set", file=sys.stderr)
        sys.exit(2)

    db_name = _check_database_safety(dsn)

    conn = await asyncpg.connect(dsn)
    try:
        await _ensure_schema(conn)
        n = await _truncate_and_seed(conn)
    finally:
        await conn.close()

    print(
        f"Seeded '{db_name}': truncated + inserted {n} chats over the past "
        f"21 days. Sidebar-only fixture (no messages)."
    )


if __name__ == "__main__":
    asyncio.run(main())

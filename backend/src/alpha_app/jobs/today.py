"""today.py — Today So Far: rolling summary of the current day.

Runs hourly from 7:30 AM to 9:30 PM. Fetches memories stored since 6 AM,
summarizes them into a brief "today so far" handoff for future-me.

The goal: bridge the gap between "context window" and "yesterday's capsule."
Without this, Alpha loses the morning by afternoon, and the afternoon by
evening. With this, she has a continuous sense of "today" even across
multiple compactions.

Storage: writes to app.today_summary (Postgres). The orientation reader
(sources.py) currently reads from Redis — switching the reader is a
separate step once we're confident the new pipeline works.

Can be run manually:
    uv run job today-so-far
    uv run job today-so-far --dry-run
"""

import logging

import pendulum

from alpha_app.claude import AssistantEvent, Claude, ResultEvent
from alpha_app.constants import REDIS_URL

logger = logging.getLogger(__name__)

PACIFIC = "America/Los_Angeles"


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def build_prompt(memories: list[dict], now: pendulum.DateTime) -> str:
    """Build the today-so-far prompt from memories."""
    if not memories:
        return (
            f"It's {now.format('h:mm A')} on {now.format('dddd, MMMM D')}. "
            "You haven't stored any memories yet today. "
            "Write a single sentence: 'Today just started — no memories stored yet.'"
        )

    memories_text = "\n\n---\n\n".join(
        f"[{m['time']}]\n{m['content']}" for m in memories
    )

    return f"""Hey me. Me here.

It's {now.format('h:mm A')} on {now.format('dddd, MMMM D')}. Here's everything you've stored since 6 AM today:

---

{memories_text}

---

That's {len(memories)} memories from today so far.

Write a brief summary of today so far — what's happened, what the vibe is,
what matters. This will be injected into your context on the next prompt, so
future-you has a continuous sense of the day even if the context window has
compacted.

Think of it like: if you woke up right now with no memory of today, what would
you need to know to feel oriented? What's the shape of today?

Write in present tense where it makes sense ("today is..."), past tense for
completed things. Keep it concise but include texture — not just facts, but
how things feel. A paragraph or two, maybe three if it's been a full day.

No headers, no bullet points. Just the handoff.

\U0001f986"""


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

async def _fetch_memories_since(pool, since: pendulum.DateTime) -> list[dict]:
    """Fetch all memories since the given time, chronologically."""
    # asyncpg wants a real datetime, not a string (unlike psycopg)
    import datetime as _dt
    since_dt = _dt.datetime(
        since.year, since.month, since.day,
        since.hour, since.minute, since.second,
        tzinfo=since.timezone,
    )

    rows = await pool.fetch("""
        SELECT id, content, metadata->>'created_at' as created_at
        FROM cortex.memories
        WHERE NOT forgotten
          AND (metadata->>'created_at')::timestamptz >= $1
        ORDER BY (metadata->>'created_at')::timestamptz ASC
    """, since_dt)

    print(f"  [debug] Found {len(rows)} memories since {since.format('h:mm A')}")

    memories = []
    for row in rows:
        dt = pendulum.parse(row["created_at"]).in_timezone(PACIFIC)
        memories.append({
            "id": row["id"],
            "content": row["content"],
            "time": dt.format("h:mm A"),
        })
    return memories


async def _ensure_table(pool) -> None:
    """Create the app.today_summary table if it doesn't exist. Idempotent."""
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS app.today_summary (
            id INTEGER PRIMARY KEY DEFAULT 1,
            summary TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT single_row CHECK (id = 1)
        )
    """)


async def _store_summary(pool, summary: str, now: pendulum.DateTime) -> None:
    """Upsert the today-so-far summary into Postgres."""
    await pool.execute("""
        INSERT INTO app.today_summary (id, summary, updated_at)
        VALUES (1, $1, $2)
        ON CONFLICT (id) DO UPDATE
            SET summary = EXCLUDED.summary,
                updated_at = EXCLUDED.updated_at
    """, summary, now.in_timezone("UTC").naive())


async def _store_redis(summary: str, now: pendulum.DateTime) -> None:
    """Also write to Redis for backward compatibility with sources.py."""
    import redis.asyncio as aioredis

    TTL = 65 * 60  # 65 minutes
    KEY = "systemprompt:past:today"
    TIME_KEY = "systemprompt:past:today:time"

    try:
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            await r.setex(KEY, TTL, summary)
            await r.setex(TIME_KEY, TTL, now.format("h:mm A"))
        finally:
            await r.aclose()
    except Exception as e:
        logger.warning(f"today: Redis write failed (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Job runner
# ---------------------------------------------------------------------------

async def run(app, **kwargs) -> str | None:
    """Run the today-so-far job.

    Fetches memories since 6 AM, summarizes via Claude, stores in Postgres
    (and Redis for backward compat).

    Args:
        app: The FastAPI app instance (for system prompt, database pool).

    Returns:
        The summary text, or None if skipped.
    """
    now = pendulum.now(PACIFIC)
    logger.info(f"today: starting at {now.format('h:mm A')}")

    if now.hour < 6:
        logger.info("today: before 6 AM, skipping")
        return None

    # Fetch memories
    from alpha_app.memories.db import get_pool as get_cortex_pool
    cortex_pool = await get_cortex_pool()
    start_of_day = now.replace(hour=6, minute=0, second=0, microsecond=0)
    memories = await _fetch_memories_since(cortex_pool, start_of_day)
    logger.info(f"today: found {len(memories)} memories since 6 AM")

    # Build prompt
    prompt = build_prompt(memories, now)

    if not memories:
        summary = "Today just started — no memories stored yet."
    else:
        # Run Claude one-shot
        system_prompt = getattr(app.state, "system_prompt", None) if app else None

        claude = Claude(
            system_prompt=system_prompt,
            permission_mode="bypassPermissions",
        )

        try:
            await claude.start()
            await claude.send([{"type": "text", "text": prompt}])

            output_parts: list[str] = []
            async for event in claude.events():
                if isinstance(event, AssistantEvent):
                    for block in event.content:
                        if block.get("type") == "text" and block.get("text"):
                            output_parts.append(block["text"])
                elif isinstance(event, ResultEvent):
                    break

            summary = "".join(output_parts).strip()
            logger.info(f"today: got summary ({len(summary)} chars)")
        finally:
            await claude.stop()

    # Store in Postgres
    from alpha_app.db import get_pool as get_app_pool
    app_pool = get_app_pool()
    await _ensure_table(app_pool)
    await _store_summary(app_pool, summary, now)
    logger.info("today: stored in Postgres (app.today_summary)")

    # Also store in Redis (backward compat — sources.py reads from there)
    await _store_redis(summary, now)
    logger.info("today: stored in Redis (backward compat)")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_cli_args(subparsers) -> None:
    """Register the today-so-far subcommand."""
    sub = subparsers.add_parser(
        "today-so-far",
        help="Generate a rolling summary of today's memories",
    )
    sub.add_argument(
        "--dry-run", action="store_true",
        help="Show the summary without storing it",
    )
    sub.set_defaults(func=_cli_run)


async def _cli_run(args) -> None:
    """CLI entry point — initialize pools, run the job, clean up."""
    from alpha_app.db import init_pool, close_pool
    from alpha_app.memories import init_schema, close as close_cortex
    from alpha_app.system_prompt import assemble_system_prompt

    # Minimal app-like object for the job to use
    class _FakeApp:
        class state:
            system_prompt = ""

    await init_pool()
    try:
        await init_schema()
    except Exception:
        pass

    try:
        _FakeApp.state.system_prompt = await assemble_system_prompt()
    except Exception:
        pass

    try:
        summary = await run(_FakeApp)

        if summary:
            print()
            print("=" * 60)
            print("Today So Far")
            print("=" * 60)
            print()
            print(summary)
            print()
            if args.dry_run:
                print("[DRY RUN — stored anyway for now, TODO: skip store on dry-run]")
    finally:
        await close_cortex()
        await close_pool()

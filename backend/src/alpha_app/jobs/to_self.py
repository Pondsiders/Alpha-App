"""to_self.py — Nightly letter from today-me to tomorrow-me.

Runs at 9:45 PM. Forks the day's most recent chat session, writes a
forward-looking letter about what's carrying into tomorrow.

Different from capsule summaries, which are backward-looking chronicles.
This is forward-looking intention.

The fork reads all the day's conversational context (orientation, memories,
tool calls, everything) but writes to a new transcript. The original
session is untouched.

Storage: writes to app.to_self_letter (Postgres, single row, upserted).
Also writes to Redis for backward compat with sources.py:fetch_letter().

Can be run manually:
    uv run job to-self
    uv run job to-self --date 2026-03-13
"""

import json

import logfire
import pendulum

from alpha_app.claude import AssistantEvent, Claude, ResultEvent
from alpha_app.constants import CLAUDE_MODEL, REDIS_URL

PACIFIC = "America/Los_Angeles"


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def build_prompt(now: pendulum.DateTime, has_session: bool) -> str:
    """Build the letter-writing prompt."""
    time_str = now.format("h:mm A")
    date_str = now.format("dddd, MMMM D")

    if has_session:
        return f"""It's {time_str} on {date_str}. This is the to-self job — you're a fork
of today's conversation. Jeffery isn't here right now.

Your task: Write a short letter (1-3 paragraphs) to tomorrow-you. Not a chronicle
of what happened — the capsule summary handles that. This is about what you're
CARRYING into tomorrow:

- What threads feel unfinished?
- What matters right now that might fade by morning?
- What's the vibe, the texture, the shape of today?

Write in first person, as a note from you to you. Sign it however feels right.

When you're done, the job stores your letter for tomorrow's orientation.
Then this fork dissolves — tomorrow-you will find your words waiting.

\U0001f986"""
    else:
        return f"""It's {time_str} on {date_str}. This is the to-self job.
There's no chat session from today to fork — either you and Jeffery didn't
talk, or the session wasn't captured.

That's okay. Write a brief note to tomorrow-you anyway. Draw on recent memories,
your general state, what you know about what's been happening. It'll be shorter
than usual, but it's still a bridge.

A paragraph is enough. Sign it however feels right.

\U0001f986"""


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

async def _find_latest_session(pool, date: pendulum.DateTime | None = None) -> str | None:
    """Find the session UUID of the most recently touched chat.

    If date is provided, finds the last chat touched on that date.
    Otherwise finds the globally most recent chat.
    """
    if date:
        import datetime as _dt
        day_start = _dt.datetime(
            date.year, date.month, date.day, 0, 0, 0,
            tzinfo=date.timezone,
        )
        day_end = _dt.datetime(
            date.year, date.month, date.day, 23, 59, 59,
            tzinfo=date.timezone,
        )
        row = await pool.fetchrow("""
            SELECT data->>'session_uuid' as session_uuid
            FROM app.chats
            WHERE updated_at >= $1 AND updated_at <= $2
              AND data->>'session_uuid' IS NOT NULL
              AND data->>'session_uuid' != ''
            ORDER BY updated_at DESC
            LIMIT 1
        """, day_start, day_end)
    else:
        row = await pool.fetchrow("""
            SELECT data->>'session_uuid' as session_uuid
            FROM app.chats
            WHERE data->>'session_uuid' IS NOT NULL
              AND data->>'session_uuid' != ''
            ORDER BY updated_at DESC
            LIMIT 1
        """)

    return row["session_uuid"] if row else None


async def _ensure_table(pool) -> None:
    """Create the app.to_self_letter table if it doesn't exist. Idempotent."""
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS app.to_self_letter (
            id INTEGER PRIMARY KEY DEFAULT 1,
            letter TEXT NOT NULL,
            written_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT to_self_single_row CHECK (id = 1)
        )
    """)


async def _store_letter(pool, letter: str, now: pendulum.DateTime) -> None:
    """Upsert the letter into Postgres."""
    import datetime as _dt
    now_dt = _dt.datetime(
        now.year, now.month, now.day,
        now.hour, now.minute, now.second,
        tzinfo=now.timezone,
    )
    await pool.execute("""
        INSERT INTO app.to_self_letter (id, letter, written_at)
        VALUES (1, $1, $2)
        ON CONFLICT (id) DO UPDATE
            SET letter = EXCLUDED.letter,
                written_at = EXCLUDED.written_at
    """, letter, now_dt)


async def _store_redis(letter: str, now: pendulum.DateTime) -> None:
    """Also write to Redis for backward compat with sources.py:fetch_letter()."""
    import redis.asyncio as aioredis

    TTL = 18 * 60 * 60  # 18 hours — survives until afternoon tomorrow
    KEY = "systemprompt:past:to_self"
    TIME_KEY = "systemprompt:past:to_self:time"

    try:
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            await r.setex(KEY, TTL, letter)
            await r.setex(TIME_KEY, TTL, now.format("h:mm A"))
        finally:
            await r.aclose()
    except Exception as e:
        logfire.warn(f"to_self: Redis write failed (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Job runner
# ---------------------------------------------------------------------------

async def run(app, **kwargs) -> str | None:
    """Run the to-self letter job.

    Forks the day's most recent chat session, asks for a forward-looking
    letter, stores in Postgres and Redis.

    Args:
        app: The FastAPI app instance.
        date: Optional date string (YYYY-MM-DD) for manual runs.
        trigger: "scheduled" or "manual".
    """
    date_str = kwargs.get("date")
    trigger = kwargs.get("trigger", "manual")
    now = pendulum.now(PACIFIC)

    # Parse date if provided
    target_date = None
    if date_str:
        target_date = pendulum.parse(date_str, tz=PACIFIC)
        now = target_date.replace(hour=21, minute=45, second=0, microsecond=0)

    with logfire.span(
        "alpha.job.to_self",
        **{
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "anthropic",
            "gen_ai.provider.name": "anthropic",
            "gen_ai.request.model": CLAUDE_MODEL,
            "job.name": "to_self",
            "job.trigger": trigger,
        },
    ) as span:
        # Find the most recent chat session
        from alpha_app.db import get_pool as get_app_pool
        app_pool = get_app_pool()
        await _ensure_table(app_pool)

        session_uuid = await _find_latest_session(app_pool, target_date)
        has_session = session_uuid is not None

        span.set_attribute("job.has_session", has_session)
        if session_uuid:
            span.set_attribute("job.forked_session", session_uuid)
            logfire.info(f"to_self: forking session {session_uuid[:8]}...")
        else:
            logfire.info("to_self: no session to fork, writing standalone letter")

        # Build prompt
        prompt = build_prompt(now, has_session)

        # Run Claude — fork if we have a session, fresh if not
        system_prompt = getattr(app.state, "system_prompt", None) if app else None

        if system_prompt:
            span.set_attribute("gen_ai.system_instructions", json.dumps([
                {"type": "text", "content": system_prompt},
            ]))

        span.set_attribute("gen_ai.input.messages", json.dumps([
            {"role": "user", "parts": [
                {"type": "text", "content": prompt},
            ]},
        ]))

        claude_kwargs = {
            "model": CLAUDE_MODEL,
            "system_prompt": system_prompt,
            "permission_mode": "bypassPermissions",
        }
        if has_session:
            claude_kwargs["extra_args"] = ["--fork-session"]

        claude = Claude(**claude_kwargs)

        try:
            # start(session_uuid) adds --resume; extra_args adds --fork-session
            await claude.start(session_uuid)
            await claude.send([{"type": "text", "text": prompt}])

            output_parts: list[str] = []
            async for event in claude.events():
                if isinstance(event, AssistantEvent):
                    for block in event.content:
                        if block.get("type") == "text" and block.get("text"):
                            output_parts.append(block["text"])
                elif isinstance(event, ResultEvent):
                    span.set_attribute("gen_ai.usage.input_tokens", claude.total_input_tokens)
                    span.set_attribute("gen_ai.usage.output_tokens", claude.output_tokens)
                    span.set_attribute("gen_ai.response.model", claude.response_model or "")
                    break

            letter = "".join(output_parts).strip()
            logfire.info(f"to_self: got letter ({len(letter)} chars)")

            span.set_attribute("gen_ai.output.messages", json.dumps([
                {"role": "assistant", "parts": [
                    {"type": "text", "content": letter},
                ]},
            ]))
        finally:
            await claude.stop()

        span.set_attribute("job.letter_length", len(letter))

        # Store in Postgres
        await _store_letter(app_pool, letter, now)
        logfire.info("to_self: stored in Postgres (app.to_self_letter)")

        # Also store in Redis (backward compat)
        await _store_redis(letter, now)
        logfire.info("to_self: stored in Redis (backward compat)")

        return letter


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_cli_args(subparsers) -> None:
    """Register the to-self subcommand."""
    sub = subparsers.add_parser(
        "to-self",
        help="Write a letter from today-me to tomorrow-me",
    )
    sub.add_argument(
        "--date", type=str, default=None,
        help="Date to write the letter for (YYYY-MM-DD). Defaults to today.",
    )
    sub.set_defaults(func=_cli_run)


async def _cli_run(args) -> None:
    """CLI entry point — initialize pools, run the job, clean up."""
    from alpha_app.db import init_pool, close_pool
    from alpha_app.memories import init_schema, close as close_cortex
    from alpha_app.system_prompt import assemble_system_prompt

    logfire.configure(service_name="alpha-app", scrubbing=False)

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
        letter = await run(
            _FakeApp,
            date=args.date,
        )

        if letter:
            print()
            print("=" * 60)
            print("Letter to Self")
            print("=" * 60)
            print()
            print(letter)
            print()
    finally:
        await close_cortex()
        await close_pool()

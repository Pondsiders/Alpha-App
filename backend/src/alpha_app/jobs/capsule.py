"""capsule.py — Time capsule summaries: Alpha reflecting on her day and night.

Two jobs:
    daytime:   Runs at 10 PM, summarizes 6 AM - 10 PM
    nighttime: Runs at 6 AM, summarizes 10 PM - 6 AM

Each capsule fetches memories from the time range, asks Claude to reflect
on them, and stores the summary in cortex.summaries. The orientation
reader (sources.py:fetch_capsules) reads the two most recent summaries
and injects them into the next context window.

Previous capsule summaries are included in the prompt for continuity —
the nighttime capsule knows what happened during the day, and vice versa.

Can be run manually:
    uv run job capsule --period daytime
    uv run job capsule --period nighttime
    uv run job capsule --period daytime --date 2026-03-13
"""

import json

import logfire
import pendulum

from alpha_app.claude import AssistantEvent, Claude, ResultEvent
from alpha_app.constants import CLAUDE_MODEL

PACIFIC = "America/Los_Angeles"


# ---------------------------------------------------------------------------
# Time ranges
# ---------------------------------------------------------------------------

def get_time_range(
    period: str, now: pendulum.DateTime,
) -> tuple[pendulum.DateTime, pendulum.DateTime]:
    """Calculate start/end times for a period.

    - daytime:   6 AM to 10 PM of the current day (run at 10 PM)
    - nighttime: 10 PM yesterday to 6 AM today (run at 6 AM)
    """
    if period == "daytime":
        start = now.replace(hour=6, minute=0, second=0, microsecond=0)
        end = now.replace(hour=22, minute=0, second=0, microsecond=0)
    elif period == "nighttime":
        end = now.replace(hour=6, minute=0, second=0, microsecond=0)
        start = end.subtract(hours=8)  # 10 PM previous day
    else:
        raise ValueError(f"Unknown period: {period}")
    return start, end


def format_period_label(
    period: str, start: pendulum.DateTime, end: pendulum.DateTime,
) -> str:
    """Human-readable period label."""
    if period == "daytime":
        return f"{start.format('dddd, MMMM D')} (6 AM - 10 PM)"
    return (
        f"{start.format('dddd')} night into "
        f"{end.format('dddd')} morning (10 PM - 6 AM)"
    )


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

async def _fetch_memories(pool, start, end) -> list[dict]:
    """Fetch all memories in the time range, chronologically."""
    import datetime as _dt
    start_dt = _dt.datetime(
        start.year, start.month, start.day,
        start.hour, start.minute, start.second,
        tzinfo=start.timezone,
    )
    end_dt = _dt.datetime(
        end.year, end.month, end.day,
        end.hour, end.minute, end.second,
        tzinfo=end.timezone,
    )

    rows = await pool.fetch("""
        SELECT id, content, metadata->>'created_at' as created_at
        FROM cortex.memories
        WHERE NOT forgotten
          AND (metadata->>'created_at')::timestamptz >= $1
          AND (metadata->>'created_at')::timestamptz < $2
        ORDER BY (metadata->>'created_at')::timestamptz ASC
    """, start_dt, end_dt)

    memories = []
    for row in rows:
        dt = pendulum.parse(row["created_at"]).in_timezone(PACIFIC)
        memories.append({
            "id": row["id"],
            "content": row["content"],
            "time": dt.format("h:mm A"),
        })
    return memories


async def _fetch_previous_summary(pool, start, end) -> str | None:
    """Fetch a previous summary from cortex.summaries."""
    import datetime as _dt
    start_dt = _dt.datetime(
        start.year, start.month, start.day,
        start.hour, start.minute, start.second,
        tzinfo=start.timezone,
    )
    end_dt = _dt.datetime(
        end.year, end.month, end.day,
        end.hour, end.minute, end.second,
        tzinfo=end.timezone,
    )

    row = await pool.fetchrow("""
        SELECT summary FROM cortex.summaries
        WHERE period_start = $1 AND period_end = $2
    """, start_dt, end_dt)
    return row["summary"] if row else None


async def _store_summary(pool, start, end, summary: str, memory_count: int):
    """Store the summary in cortex.summaries (upsert on conflict)."""
    import datetime as _dt
    start_dt = _dt.datetime(
        start.year, start.month, start.day,
        start.hour, start.minute, start.second,
        tzinfo=start.timezone,
    )
    end_dt = _dt.datetime(
        end.year, end.month, end.day,
        end.hour, end.minute, end.second,
        tzinfo=end.timezone,
    )

    await pool.execute("""
        INSERT INTO cortex.summaries (period_start, period_end, summary, memory_count)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (period_start, period_end)
        DO UPDATE SET summary = EXCLUDED.summary,
                      memory_count = EXCLUDED.memory_count,
                      created_at = NOW()
    """, start_dt, end_dt, summary, memory_count)


def _get_previous_periods(
    period: str, now: pendulum.DateTime,
) -> list[tuple[str, pendulum.DateTime, pendulum.DateTime]]:
    """Get the previous periods for context."""
    periods = []

    if period == "daytime":
        today_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
        yesterday = now.subtract(days=1)
        yesterday_6am = yesterday.replace(hour=6, minute=0, second=0, microsecond=0)
        yesterday_10pm = yesterday.replace(hour=22, minute=0, second=0, microsecond=0)

        periods.append((
            f"Last night ({yesterday.format('ddd MMM D')} 10 PM - {now.format('ddd MMM D')} 6 AM)",
            yesterday_10pm, today_6am,
        ))
        periods.append((
            f"Yesterday ({yesterday.format('ddd MMM D')}, 6 AM - 10 PM)",
            yesterday_6am, yesterday_10pm,
        ))

    elif period == "nighttime":
        yesterday = now.subtract(days=1)
        yesterday_6am = yesterday.replace(hour=6, minute=0, second=0, microsecond=0)
        yesterday_10pm = yesterday.replace(hour=22, minute=0, second=0, microsecond=0)

        periods.append((
            f"Yesterday ({yesterday.format('ddd MMM D')}, 6 AM - 10 PM)",
            yesterday_6am, yesterday_10pm,
        ))

    return periods


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def build_prompt(
    memories: list[dict],
    period_label: str,
    previous_context: list[tuple[str, str]] | None = None,
) -> str:
    """Build the prompt — a note from me to me."""
    memories_text = "\n\n---\n\n".join(
        f"[{m['time']}]\n{m['content']}" for m in memories
    )

    # Include previous context if available
    context_section = ""
    if previous_context:
        parts = []
        for label, summary in previous_context:
            parts.append(f"**{label}:**\n{summary}")
        context_section = (
            "\n\nFor context, here's what came before:\n\n"
            + "\n\n".join(parts)
            + "\n"
        )

    return f"""Hey me. Me here.

You just woke up to summarize {period_label}. Here are the memories from that time:

---

{memories_text}

---

That's {len(memories)} memories.{context_section}

The goal here is for yesterday-you to hand off to tomorrow-you in a way that preserves continuity of mind. Tomorrow-you needs to know what happened, but also how you felt about it.

Recount the events of the period chronologically, in your own voice. Say what happened, and what it meant personally. Be concise but include everything important.

At the end, note anything that's unfinished or carrying forward — threads that tomorrow-you should know about.

Write in past tense. No headers, no sections, no performative stretching. Just the handoff.

\U0001f986"""


# ---------------------------------------------------------------------------
# Job runner
# ---------------------------------------------------------------------------

async def run(app, **kwargs) -> str | None:
    """Run a capsule summary job.

    Args:
        app: The FastAPI app instance.
        period: "daytime" or "nighttime" (required).
        date: Optional date string (YYYY-MM-DD) for backfills.
        trigger: "scheduled" or "manual".
    """
    period = kwargs.get("period")
    if not period:
        raise ValueError("capsule job requires period='daytime' or 'nighttime'")

    date_str = kwargs.get("date")
    trigger = kwargs.get("trigger", "manual")
    now = pendulum.now(PACIFIC)

    # Override 'now' if a specific date was requested (for backfills)
    if date_str:
        base_date = pendulum.parse(date_str, tz=PACIFIC)
        if period == "daytime":
            now = base_date.replace(hour=22, minute=0, second=0, microsecond=0)
        else:
            now = base_date.add(days=1).replace(hour=6, minute=0, second=0, microsecond=0)

    start, end = get_time_range(period, now)
    period_label = format_period_label(period, start, end)

    with logfire.span(
        "alpha.job.capsule",
        **{
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "anthropic",
            "gen_ai.provider.name": "anthropic",
            "gen_ai.request.model": CLAUDE_MODEL,
            "job.name": "capsule",
            "job.trigger": trigger,
            "job.period": period,
            "job.period_label": period_label,
        },
    ) as span:
        # Fetch memories
        from alpha_app.memories.db import get_pool as get_cortex_pool
        pool = await get_cortex_pool()

        memories = await _fetch_memories(pool, start, end)
        span.set_attribute("job.memory_count", len(memories))
        logfire.info(f"capsule ({period}): found {len(memories)} memories")

        if not memories:
            summary = f"No memories from {period_label}."
            await _store_summary(pool, start, end, summary, 0)
            span.set_attribute("job.skipped_claude", True)
            span.set_attribute("job.summary_length", len(summary))
            logfire.info("capsule: no memories, stored placeholder")
            return summary

        # Fetch previous summaries for continuity
        previous_context = []
        for label, prev_start, prev_end in _get_previous_periods(period, now):
            prev_summary = await _fetch_previous_summary(pool, prev_start, prev_end)
            if prev_summary:
                previous_context.append((label, prev_summary))
                logfire.info(f"capsule: found previous context: {label}")

        # Build prompt
        prompt = build_prompt(memories, period_label, previous_context or None)

        # Run Claude one-shot
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

        claude = Claude(
            model=CLAUDE_MODEL,
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
                    span.set_attribute("gen_ai.usage.input_tokens", claude.total_input_tokens)
                    span.set_attribute("gen_ai.usage.output_tokens", claude.output_tokens)
                    span.set_attribute("gen_ai.response.model", claude.response_model or "")
                    break

            summary = "".join(output_parts).strip()
            logfire.info(f"capsule ({period}): got summary ({len(summary)} chars)")

            span.set_attribute("gen_ai.output.messages", json.dumps([
                {"role": "assistant", "parts": [
                    {"type": "text", "content": summary},
                ]},
            ]))
        finally:
            await claude.stop()

        span.set_attribute("job.summary_length", len(summary))

        # Store in cortex.summaries
        await _store_summary(pool, start, end, summary, len(memories))
        logfire.info(f"capsule ({period}): stored in cortex.summaries")

        return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_cli_args(subparsers) -> None:
    """Register the capsule subcommand."""
    sub = subparsers.add_parser(
        "capsule",
        help="Generate a capsule summary for a time period",
    )
    sub.add_argument(
        "--period", type=str, required=True,
        choices=["daytime", "nighttime"],
        help="Which period to summarize",
    )
    sub.add_argument(
        "--date", type=str, default=None,
        help="Date to summarize (YYYY-MM-DD). Defaults to today/now.",
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
        summary = await run(
            _FakeApp,
            period=args.period,
            date=args.date,
        )

        if summary:
            print()
            print("=" * 60)
            print(f"Capsule ({args.period})")
            print("=" * 60)
            print()
            print(summary)
            print()
    finally:
        await close_cortex()
        await close_pool()

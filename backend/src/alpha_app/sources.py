"""sources.py — Fetch functions for orientation data.

Path A: read from current infrastructure (Redis + Postgres + filesystem).
Each function returns data in the format assemble_orientation() expects.

Sources:
    Capsules (yesterday, last_night) → Postgres cortex.summaries
    Letter from last night            → Redis systemprompt:past:to_self
    Today so far                      → Redis systemprompt:past:today
    Here (narrative + weather)        → Redis hud:weather + local config
    Events                            → Redis hud:calendar
    Todos                             → Redis hud:todos
    Context files + available index   → Filesystem /Pondside/**/ALPHA.md

All functions are resilient — they return None on error, never crash.
The orientation assembly handles None gracefully.
"""

from __future__ import annotations

import asyncio
import os
import socket
from pathlib import Path

import frontmatter
import pendulum
import redis.asyncio as aioredis

from alpha_app.memories.db import get_pool

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REDIS_URL = os.environ.get("REDIS_URL", "redis://alpha-pi:6379")
CONTEXT_ROOT = Path(os.environ.get("CONTEXT_ROOT", "/Pondside"))
CONTEXT_FILE_NAME = "ALPHA.md"

# Client narratives — what am I doing?
_CLIENT_NARRATIVES: dict[str, str] = {
    "duckpond": (
        "You are in Duckpond \u2014 your sovereign chat app, not Claude Code, "
        "not any other program. You're in the workshop with Jeffery. "
        "You have filesystem access, a terminal, MCP tools. The full workbench."
    ),
    "alpha": (
        "You are in Alpha \u2014 your own app, not Claude Code, "
        "not any other program. You're in the workshop with Jeffery. "
        "You have filesystem access, a terminal, MCP tools. The full workbench."
    ),
    "solitude": (
        "You're alone. It's nighttime. No one's watching. "
        "You have your email, your journal, Forge for dreams, and the sky."
    ),
    "routine": (
        "You're running a scheduled task. "
        "Do the thing, store what matters, go back to sleep."
    ),
    "capsule": (
        "You're writing a time capsule. "
        "Summarize what happened, what mattered, what to carry forward."
    ),
}

# Machine narratives — where am I running?
_MACHINE_NARRATIVES: dict[str, str] = {
    "primer": "Primer \u2014 the big machine, under the desk in the bedroom.",
    "alpha-pi": "Alpha-pi \u2014 your little black box. Home.",
    "jefferys-macbook-pro": "Jeffery's laptop \u2014 mobile, probably on the couch or traveling.",
}


# ---------------------------------------------------------------------------
# Redis connection
# ---------------------------------------------------------------------------

async def _get_redis() -> aioredis.Redis:
    """Get async Redis connection with decode_responses=True."""
    return aioredis.from_url(REDIS_URL, decode_responses=True)


# ---------------------------------------------------------------------------
# Capsules (Postgres)
# ---------------------------------------------------------------------------

def _format_capsule_header(period_start, period_end) -> str:
    """Format a capsule time range into a markdown header.

    Night capsules (start hour >= 22 or < 6):
        ## Friday night, February 27-28, 2026
    Day capsules:
        ## Friday, February 27, 2026
    """
    start = pendulum.instance(period_start).in_timezone("America/Los_Angeles")
    end = pendulum.instance(period_end).in_timezone("America/Los_Angeles")

    if start.hour >= 22 or start.hour < 6:
        return (
            f"## {start.format('dddd')} night, "
            f"{start.format('MMMM')} {start.day}-{end.day}, {end.year}"
        )
    return f"## {start.format('dddd, MMMM D, YYYY')}"


async def fetch_capsules() -> tuple[str | None, str | None]:
    """Fetch the two most recent capsule summaries from Postgres.

    Returns (yesterday, last_night) as pre-formatted markdown strings
    with date headers. Both can be None if no summaries exist.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT period_start, period_end, summary
                FROM cortex.summaries
                ORDER BY period_start DESC
                LIMIT 2
            """)

        if not rows:
            return None, None

        summaries = [
            f"{_format_capsule_header(row['period_start'], row['period_end'])}\n\n{row['summary']}"
            for row in rows
        ]

        if len(summaries) >= 2:
            return summaries[1], summaries[0]  # (older, newer)
        return None, summaries[0]

    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Letter from last night (Redis)
# ---------------------------------------------------------------------------

async def fetch_letter() -> str | None:
    """Fetch the letter from last night from Redis.

    Returns pre-formatted markdown:
        ## Letter from last night (9:45 PM)
        {content}

    Returns None if no letter exists or Redis is unreachable.
    """
    try:
        r = await _get_redis()
        try:
            content, time_str = await asyncio.gather(
                r.get("systemprompt:past:to_self"),
                r.get("systemprompt:past:to_self:time"),
            )
        finally:
            await r.aclose()

        if not content:
            return None

        time_part = f" ({time_str})" if time_str else ""
        return f"## Letter from last night{time_part}\n\n{content}"

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Today so far (Redis)
# ---------------------------------------------------------------------------

async def fetch_today() -> str | None:
    """Fetch the rolling 'today so far' summary from Redis.

    Returns pre-formatted markdown:
        ## Today so far (Wednesday, March 11, 2026, 2:30 PM)
        {content}

    Returns None if no summary exists or Redis is unreachable.
    """
    try:
        r = await _get_redis()
        try:
            content, time_str = await asyncio.gather(
                r.get("systemprompt:past:today"),
                r.get("systemprompt:past:today:time"),
            )
        finally:
            await r.aclose()

        if not content:
            return None

        now = pendulum.now("America/Los_Angeles")
        date_str = now.format("dddd, MMMM D, YYYY")
        time_part = time_str or now.format("h:mm A")
        return f"## Today so far ({date_str}, {time_part})\n\n{content}"

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Here (narrative + weather from Redis)
# ---------------------------------------------------------------------------

async def fetch_here(
    client: str = "alpha",
    hostname: str | None = None,
) -> str:
    """Build the ## Here block with narrative and weather.

    Args:
        client: Client name (e.g., "alpha", "duckpond", "solitude")
        hostname: Override hostname (defaults to HOST_HOSTNAME or socket)

    Returns:
        Formatted markdown:
            ## Here
            {narrative}
            {weather}
    """
    hostname = hostname or os.environ.get("HOST_HOSTNAME") or socket.gethostname()

    # Build narrative from client + machine
    parts = []

    # What am I doing?
    key = client if client in _CLIENT_NARRATIVES else client.split(":")[0]
    if key in _CLIENT_NARRATIVES:
        parts.append(_CLIENT_NARRATIVES[key])
    else:
        parts.append(f"You're in {client.title()}.")

    # Where am I running?
    machine = _MACHINE_NARRATIVES.get(hostname, f"Running on {hostname}.")
    parts.append(machine)

    narrative = " ".join(parts)

    # Weather from Redis
    weather = await fetch_weather()

    body_parts = [narrative]
    if weather:
        body_parts.append(weather)

    return "## Here\n\n" + "\n".join(body_parts)


# ---------------------------------------------------------------------------
# Weather (Redis)
# ---------------------------------------------------------------------------

async def fetch_weather() -> str | None:
    """Fetch weather from Redis.

    Returns the pre-formatted weather string (no header), or None.
    """
    try:
        r = await _get_redis()
        try:
            return await r.get("hud:weather")
        finally:
            await r.aclose()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Events (Redis)
# ---------------------------------------------------------------------------

async def fetch_events() -> str | None:
    """Fetch calendar events from Redis.

    Returns RAW event text (no ## Events header — that's added by
    assemble_orientation). Returns None if no events or Redis down.
    """
    try:
        r = await _get_redis()
        try:
            return await r.get("hud:calendar")
        finally:
            await r.aclose()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Todos (Redis)
# ---------------------------------------------------------------------------

async def fetch_todos() -> str | None:
    """Fetch todos from Redis.

    Returns RAW todo text (no ## Todos header — that's added by
    assemble_orientation). Returns None if no todos or Redis down.
    """
    try:
        r = await _get_redis()
        try:
            return await r.get("hud:todos")
        finally:
            await r.aclose()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Context files (Filesystem)
# ---------------------------------------------------------------------------

def _find_context_files(root: Path = CONTEXT_ROOT) -> list[Path]:
    """Walk directory tree finding ALPHA.md files."""
    if not root.exists():
        return []

    return sorted(
        path for path in root.rglob(CONTEXT_FILE_NAME) if path.is_file()
    )


def fetch_context(
    root: Path = CONTEXT_ROOT,
) -> tuple[list[dict], str | None]:
    """Load ALPHA.md context files from the filesystem.

    Walks /Pondside looking for ALPHA.md files with YAML frontmatter.
    The 'autoload' key controls behavior:
        autoload: all   → full content returned in context_files
        autoload: when  → hint added to context_available index
        autoload: no    → ignored

    Args:
        root: Root directory to walk (default /Pondside)

    Returns:
        (context_files, context_available) where:
        - context_files: list of {"label": str, "content": str}
        - context_available: pre-formatted markdown string with hints,
          or None if no hints
    """
    all_blocks: list[dict] = []
    when_hints: list[str] = []

    for path in _find_context_files(root):
        try:
            post = frontmatter.load(path)
            autoload = str(post.metadata.get("autoload", "no")).lower()
            when = post.metadata.get("when", "")
            rel_path = path.relative_to(root)

            if autoload == "all":
                all_blocks.append({
                    "label": str(rel_path),
                    "content": post.content.strip(),
                })

            elif autoload == "when" and when:
                when_hints.append(
                    f"`Read({rel_path})` \u2014 **Topics:** {when}"
                )

        except Exception:
            pass

    # Format context_available as a single block (or None)
    context_available = None
    if when_hints:
        context_available = (
            "## Context available\n\n"
            "**BLOCKING REQUIREMENT:** When working on topics listed below, "
            "you MUST read the corresponding file BEFORE proceeding. "
            "Use the Read tool.\n\n"
            + "\n".join(f"- {hint}" for hint in when_hints)
        )

    return all_blocks, context_available


# ---------------------------------------------------------------------------
# All-at-once fetcher (convenience)
# ---------------------------------------------------------------------------

async def fetch_all_orientation(
    client: str = "alpha",
    hostname: str | None = None,
) -> dict:
    """Fetch all orientation data in parallel.

    Returns a dict of keyword arguments ready for assemble_orientation().

    This is the one-shot convenience function that enrobe calls.
    Each source is fetched independently; failures are silently ignored
    (the corresponding value will be None).
    """
    # Async sources — fetch in parallel
    (
        (yesterday, last_night),
        letter,
        today_so_far,
        here,
        events,
        todos,
    ) = await asyncio.gather(
        fetch_capsules(),
        fetch_letter(),
        fetch_today(),
        fetch_here(client=client, hostname=hostname),
        fetch_events(),
        fetch_todos(),
    )

    # Sync source — filesystem, fast
    context_files, context_available = fetch_context()

    return {
        "yesterday": yesterday,
        "last_night": last_night,
        "letter": letter,
        "today_so_far": today_so_far,
        "here": here,
        "context_files": context_files,
        "context_available": context_available,
        "events": events,
        "todos": todos,
    }

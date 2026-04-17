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
import json
import os
import socket
from pathlib import Path

import frontmatter
import pendulum
import redis.asyncio as aioredis

from alpha_app.constants import CONTEXT_FILE_NAME, REDIS_URL
from alpha_app.memories.db import get_pool

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONTEXT_ROOT = Path("/Pondside")

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

def _pondside_yesterday() -> tuple[pendulum.DateTime, pendulum.DateTime]:
    """Return the (start, end) of yesterday's Pondside day.

    A Pondside day runs 6 AM to 6 AM. "Yesterday" is the 24-hour window
    ending at the most recently passed 6 AM boundary.
    """
    from alpha_app.clock import yesterday_dawn, today_dawn
    return yesterday_dawn(), today_dawn()


async def fetch_capsules() -> tuple[str | None, str | None]:
    """Fetch yesterday's day and night capsules from cortex.capsules.

    Uses Pondside-day boundaries (6 AM to 6 AM). Returns (yesterday, last_night)
    as pre-formatted markdown strings. Both can be None if no capsules exist.
    """
    try:
        yesterday_dawn, today_dawn = _pondside_yesterday()

        pool = await get_pool()
        async with pool.acquire() as conn:
            day_row = await conn.fetchrow(
                "SELECT content, created_at FROM cortex.capsules"
                " WHERE kind = 'day'"
                "   AND created_at >= $1"
                "   AND created_at < $2"
                " ORDER BY created_at DESC LIMIT 1",
                yesterday_dawn, today_dawn,
            )
            night_row = await conn.fetchrow(
                "SELECT content, created_at FROM cortex.capsules"
                " WHERE kind = 'night'"
                "   AND created_at >= $1"
                "   AND created_at < $2"
                " ORDER BY created_at DESC LIMIT 1",
                yesterday_dawn, today_dawn,
            )

        yesterday = None
        if day_row:
            day_date = pendulum.instance(day_row["created_at"])
            header = f"## {day_date.format('dddd, MMMM D, YYYY')}"
            yesterday = f"{header}\n\n{day_row['content']}"

        last_night = None
        if night_row:
            night_date = pendulum.instance(night_row["created_at"])
            header = f"## {night_date.format('dddd')} night, {night_date.format('MMMM D, YYYY')}"
            last_night = f"{header}\n\n{night_row['content']}"

        return yesterday, last_night

    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Diary (Postgres)
# ---------------------------------------------------------------------------

async def fetch_diary() -> tuple[str | None, str | None]:
    """Fetch yesterday's and today's diary pages from cortex.diary.

    A diary "page" is all entries from one Pondside day (6 AM to 6 AM),
    concatenated with timestamp headers.

    Returns:
        (yesterday_page, today_page) — both pre-formatted markdown,
        or None if no entries exist for that day.
    """
    try:
        from alpha_app.clock import yesterday_dawn, today_dawn, now

        yd = yesterday_dawn()
        td = today_dawn()
        n = now()

        pool = await get_pool()
        async with pool.acquire() as conn:
            yesterday_rows = await conn.fetch(
                "SELECT content, created_at FROM cortex.diary"
                " WHERE created_at >= $1 AND created_at < $2"
                " ORDER BY created_at",
                yd, td,
            )
            today_rows = await conn.fetch(
                "SELECT content, created_at FROM cortex.diary"
                " WHERE created_at >= $1 AND created_at < $2"
                " ORDER BY created_at",
                td, n,
            )

        local_tz = pendulum.now().timezone

        def _format_page(rows, date_header: str) -> str | None:
            if not rows:
                return None
            parts = [f"## {date_header}"]
            for row in rows:
                ts = pendulum.instance(row["created_at"]).in_tz(local_tz).format("h:mm A")
                parts.append(f"\n[{ts}]\n\n{row['content']}")
            return "\n".join(parts)

        # PSO-8601 date format for headers
        yesterday_header = pendulum.instance(yd).format("ddd MMM D YYYY")
        today_header = pendulum.instance(td).format("ddd MMM D YYYY")

        return (
            _format_page(yesterday_rows, yesterday_header),
            _format_page(today_rows, f"{today_header} (so far)"),
        )

    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Letter from last night (Redis)
# ---------------------------------------------------------------------------

async def fetch_letter() -> str | None:
    """Read the letter to tomorrow from app.state.

    Returns pre-formatted markdown:
        ## Letter from last night
        {content}

    Returns None if no letter exists.
    """
    try:
        from alpha_app.db import get_state
        content = await get_state("letter_to_tomorrow")
        if not content:
            return None
        return f"## Letter from last night\n\n{content}"
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
# Events (gws CLI)
# ---------------------------------------------------------------------------

# Kylee: today only.  Jeffery: today through next 7 days.
_KYLEE_CALENDAR = "kyleepena@gmail.com"
_JEFFERY_CALENDAR = "primary"


async def _gws_events(calendar_id: str, days: int) -> list[dict]:
    """Fetch events from a single calendar via gws CLI.

    Returns a list of event dicts from the Google Calendar API JSON response.
    """
    now = pendulum.now("America/Los_Angeles")
    time_min = now.start_of("day").to_iso8601_string()
    time_max = now.start_of("day").add(days=days).to_iso8601_string()

    proc = await asyncio.create_subprocess_exec(
        "gws", "calendar", "events", "list",
        "--format", "json",
        "--params", json.dumps({
            "calendarId": calendar_id,
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": True,
            "orderBy": "startTime",
        }),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)

    if proc.returncode != 0 or not stdout:
        return []

    data = json.loads(stdout.decode())
    return data.get("items", [])


def _format_event(item: dict, who: str, show_date: bool = False) -> str:
    """Format a single event dict into a readable line."""
    start = item.get("start", {})
    summary = item.get("summary", "Untitled")
    start_str = start.get("dateTime", start.get("date", ""))

    if "T" in start_str:
        try:
            dt = pendulum.parse(start_str)
            if show_date:
                time_display = dt.format("ddd MMM D, h:mm A")
            else:
                time_display = dt.format("h:mm A")
        except Exception:
            time_display = start_str
    else:
        if show_date:
            try:
                dt = pendulum.parse(start_str)
                time_display = dt.format("ddd MMM D") + ", all day"
            except Exception:
                time_display = "all day"
        else:
            time_display = "all day"

    return f"- {who}: {summary} ({time_display})"


async def fetch_events() -> str | None:
    """Fetch calendar events via gws CLI.

    Kylee: today only.  Jeffery: today through next 7 days.
    Grouped into "Today" and "Coming Up" sections.

    Returns formatted markdown text (no ## Events header — that's added by
    assemble_orientation). Returns None if gws fails or no events.
    """
    try:
            # Kylee: through this Saturday.  Jeffery: through next Saturday.
        now = pendulum.now("America/Los_Angeles")
        days_to_sat = (5 - now.day_of_week) % 7 or 7  # days until Saturday
        kylee_days = days_to_sat + 1       # through end of this Saturday
        jeffery_days = days_to_sat + 8     # through end of next Saturday

        kylee_items, jeffery_items = await asyncio.gather(
            _gws_events(_KYLEE_CALENDAR, days=kylee_days),
            _gws_events(_JEFFERY_CALENDAR, days=jeffery_days),
        )

        now = pendulum.now("America/Los_Angeles")
        today_date = now.date()
        tomorrow_date = now.add(days=1).date()

        today_lines: list[str] = []
        tomorrow_lines: list[str] = []
        future_lines: list[str] = []

        # Classify all events into today / tomorrow / future
        all_events = [
            (item, "Kylee") for item in kylee_items
        ] + [
            (item, "Jeffery") for item in jeffery_items
        ]

        for item, who in all_events:
            start = item.get("start", {})
            if "date" in start and "dateTime" not in start:
                event_date = pendulum.parse(start["date"]).date()
            else:
                try:
                    event_date = pendulum.parse(
                        start.get("dateTime", "")
                    ).in_tz("America/Los_Angeles").date()
                except Exception:
                    event_date = today_date

            if event_date == today_date:
                today_lines.append(_format_event(item, who))
            elif event_date == tomorrow_date:
                tomorrow_lines.append(_format_event(item, who))
            else:
                future_lines.append(
                    _format_event(item, who, show_date=True)
                )

        if not today_lines and not tomorrow_lines and not future_lines:
            return None

        sections: list[str] = []
        if today_lines:
            sections.append("### Today\n\n" + "\n".join(today_lines))
        else:
            sections.append("### Today\n\nNothing on the calendar.")
        if tomorrow_lines:
            sections.append("### Tomorrow\n\n" + "\n".join(tomorrow_lines))
        if future_lines:
            sections.append("### Coming Up\n\n" + "\n".join(future_lines))

        return "\n\n".join(sections)

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

# ---------------------------------------------------------------------------
# Context Cards (Postgres)
# ---------------------------------------------------------------------------

CONTEXT_BUDGET = 20_000  # tokens

async def fetch_context_cards() -> str | None:
    """Fetch rolling context cards from cortex.context.

    Returns the most recent cards that fit within the token budget,
    concatenated under a # Context header. Returns None if empty.
    """
    try:
        pool = await get_pool()
        rows = await pool.fetch(
            "SELECT text, tokens, created_at FROM cortex.context"
            " ORDER BY created_at DESC"
        )

        cards = []
        total = 0
        for row in rows:
            if total + row["tokens"] > CONTEXT_BUDGET:
                break
            cards.append((row["text"], row["created_at"]))
            total += row["tokens"]

        if not cards:
            return None

        # Reverse so oldest is first — a story told in chronological order.
        # (The query fetches newest-first for budget trimming, but display
        # should read top-to-bottom as a narrative.)
        cards.reverse()

        # Prefix each card with a PSO-8601 timestamp and relative age.
        # Makes accretion legible: newer cards override older ones, and
        # stale facts show their age at a glance.
        tz = pendulum.local_timezone()
        formatted = []
        for text, created_at in cards:
            ts = pendulum.instance(created_at).in_timezone(tz)
            stamp = ts.format("ddd MMM D YYYY, h:mm A")
            age = ts.diff_for_humans()
            formatted.append(f"[{stamp} ({age})] {text}")

        return "# Context\n\n" + "\n\n".join(formatted)

    except Exception:
        return None


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
        (diary_yesterday, diary_today),
        letter,
        today_so_far,
        here,
        events,
        todos,
        context_cards,
    ) = await asyncio.gather(
        fetch_diary(),
        fetch_letter(),
        fetch_today(),
        fetch_here(client=client, hostname=hostname),
        fetch_events(),
        fetch_todos(),
        fetch_context_cards(),
    )

    # Sync source — filesystem, fast
    context_files, context_available = fetch_context()

    return {
        "yesterday": None,      # capsules — legacy, replaced by diary
        "last_night": None,     # capsules — legacy, replaced by diary
        "diary_yesterday": diary_yesterday,
        "diary_today": diary_today,
        "letter": letter,
        "today_so_far": today_so_far,
        "here": here,
        "context_files": context_files,
        "context_available": context_available,
        "events": events,
        "todos": todos,
        "context_cards": context_cards,
    }

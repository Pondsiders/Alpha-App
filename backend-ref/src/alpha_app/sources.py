"""sources.py — Fetch functions for system prompt context data.

Sources:
    Diary (yesterday + today)         → Postgres cortex.diary
    Here (narrative)                  → Local config (client + hostname)
    Context files + available index   → Filesystem /Pondside/**/ALPHA.md

All functions are resilient — return None on error, never crash.
"""

from __future__ import annotations

import asyncio
import os
import socket
from pathlib import Path

import frontmatter
import pendulum

from alpha_app.constants import CONTEXT_FILE_NAME
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
# Here (narrative)
# ---------------------------------------------------------------------------

async def fetch_here(
    client: str = "alpha",
    hostname: str | None = None,
) -> str:
    """Build the ## Here block with narrative.

    Args:
        client: Client name (e.g., "alpha", "duckpond", "solitude")
        hostname: Override hostname (defaults to HOST_HOSTNAME or socket)

    Returns:
        Formatted markdown:
            ## Here
            {narrative}
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

    return "## Here\n\n" + narrative


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
    """Fetch all context data in parallel.

    Returns a dict of keyword arguments ready for assemble_orientation().
    """
    # Async sources — fetch in parallel
    (
        (diary_yesterday, diary_today),
        here,
        context_cards,
    ) = await asyncio.gather(
        fetch_diary(),
        fetch_here(client=client, hostname=hostname),
        fetch_context_cards(),
    )

    # Sync source — filesystem, fast
    context_files, context_available = fetch_context()

    return {
        "diary_yesterday": diary_yesterday,
        "diary_today": diary_today,
        "here": here,
        "context_files": context_files,
        "context_available": context_available,
        "context_cards": context_cards,
    }

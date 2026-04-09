"""orientation.py — Assemble context blocks for the system prompt.

Two public functions:

    assemble_orientation(*, here, ...)  — pure assembly, no I/O
    get_here()  — detects runtime environment
"""

from __future__ import annotations

import os
import socket
from pathlib import Path


def get_here() -> str:
    """Detect current runtime environment and return a narrator-style string."""
    try:
        from importlib.metadata import version as pkg_version
        app_version = pkg_version("alpha-app")
    except Exception:
        app_version = "unknown"

    hostname = os.environ.get("HOST_HOSTNAME") or socket.gethostname()
    in_docker = Path("/.dockerenv").exists()
    env_phrase = "in a Docker container" if in_docker else "on bare metal"

    return (
        f"[Narrator] You are in Alpha v{app_version} "
        f"running {env_phrase} on `{hostname}`."
    )


def assemble_orientation(
    *,
    here: str,
    diary_yesterday: str | None = None,
    diary_today: str | None = None,
    context_files: list[dict] | None = None,
    context_available: str | None = None,
    context_cards: str | None = None,
) -> list[dict]:
    """Assemble context blocks for the system prompt.

    Pure assembly — takes pre-fetched source data as keyword arguments and
    returns a list of content block dicts. No I/O.

    Block order:
        diary → here → context files → context available
    """
    blocks: list[dict] = []

    def _add(text: str) -> None:
        blocks.append({"type": "text", "text": text})

    # Diary — yesterday's page and today's entries so far.
    diary_parts = []
    if diary_yesterday:
        diary_parts.append(diary_yesterday)
    if diary_today:
        diary_parts.append(diary_today)
    if diary_parts:
        _add("# Diary\n\n" + "\n\n".join(diary_parts))

    # context_cards — rolling front-of-mind knowledge, passed through as-is
    if context_cards:
        _add(context_cards)
    # here — passed through as-is
    if here:
        _add(here)

    # context_files — one block per file with ## Context: {label} header
    if context_files:
        for cf in context_files:
            _add(f"## Context: {cf['label']}\n\n{cf['content']}")

    # context_available — passed through as-is (already has ## header)
    if context_available:
        _add(context_available)

    return blocks

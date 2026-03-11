"""orientation.py — Assemble the orientation block for new context windows.

Three public functions:

    assemble_orientation(*, here, ...)  — pure assembly, no I/O
    check_venue_change(current, previous)  — pure comparison
    get_here()  — detects runtime environment

The orientation is prepended to the first user message of each new context
window. It is the "here + now" layer of the prompt architecture.
"""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path


def get_here() -> str:
    """Detect current runtime environment and return a human-readable string.

    Returns a string like: "Alpha v1.0.0 on primer, Docker, branch: main"
    """
    # App version
    try:
        from importlib.metadata import version as pkg_version

        app_version = pkg_version("alpha-app")
    except Exception:
        app_version = "unknown"

    # Hostname
    hostname = socket.gethostname()

    # Docker vs bare metal
    environment = "Docker" if Path("/.dockerenv").exists() else "bare metal"

    # Git branch
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        branch = result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        branch = "unknown"

    return f"Alpha v{app_version} on {hostname}, {environment}, branch: {branch}"


def assemble_orientation(
    *,
    here: str,
    yesterday: str | None = None,
    last_night: str | None = None,
    letter: str | None = None,
    today_so_far: str | None = None,
    weather: str | None = None,
    context_files: list[dict] | None = None,
    events: str | None = None,
    todos: str | None = None,
) -> list[dict]:
    """Assemble the orientation block for a new context window.

    Pure assembly — takes pre-fetched source data as keyword arguments and
    returns a list of content block dicts. No I/O.

    Block order:
        here → yesterday → last night → letter → today so far →
        weather → context files → events → todos

    Args:
        here: Always present. Gets a ## Here header added.
        yesterday: Passed through as-is (pre-formatted with its own header).
        last_night: Passed through as-is.
        letter: Passed through as-is.
        today_so_far: Passed through as-is.
        weather: Passed through as-is (no header added).
        context_files: List of {"label": str, "content": str}. Each gets a
                       ## Context: {label} header.
        events: Gets a ## Events header added.
        todos: Gets a ## Todos header added.

    Returns:
        List of {"type": "text", "text": "..."} dicts. None and "" sources
        are silently skipped. Empty context_files list is treated as absent.
    """
    blocks: list[dict] = []

    def _add(text: str) -> None:
        blocks.append({"type": "text", "text": text})

    # here — required, gets ## Here header
    if here:
        _add(f"## Here\n\n{here}")

    # yesterday — passed through as-is
    if yesterday:
        _add(yesterday)

    # last_night — passed through as-is
    if last_night:
        _add(last_night)

    # letter — passed through as-is
    if letter:
        _add(letter)

    # today_so_far — passed through as-is
    if today_so_far:
        _add(today_so_far)

    # weather — passed through as-is (no header)
    if weather:
        _add(weather)

    # context_files — one block per file with ## Context: {label} header
    if context_files:
        for cf in context_files:
            _add(f"## Context: {cf['label']}\n\n{cf['content']}")

    # events — gets ## Events header
    if events:
        _add(f"## Events\n\n{events}")

    # todos — gets ## Todos header
    if todos:
        _add(f"## Todos\n\n{todos}")

    return blocks


def check_venue_change(current: str, previous: str | None) -> dict | None:
    """Check if the venue has changed between context windows.

    Pure comparison. Returns a narrator-style notice block if the venues
    differ, or None if they are the same or if previous is None.

    Args:
        current: The current venue string.
        previous: The previous venue string, or None if there is no previous.

    Returns:
        A {"type": "text", "text": "..."} dict if venues differ, else None.
    """
    if previous is None:
        return None
    if current == previous:
        return None
    return {
        "type": "text",
        "text": f"[Venue changed. Was: {previous!r}. Now: {current!r}]",
    }

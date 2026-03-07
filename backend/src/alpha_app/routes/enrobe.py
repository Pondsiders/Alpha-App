"""enrobe.py — Message enrichment pipeline.

The place where user messages get wrapped in memories, intro suggestions,
approach lights, and timestamps before being sent to Claude.

The name: to enrobe is to coat something in chocolate. The user message
is the truffle center; everything we add is the shell.

Currently implements:
  1. Timestamp injection (PSO-8601 format)
  2. Approach lights (context usage warnings)

TODO:
  3. Memory recall (query extraction -> search -> dedup -> format)
  4. Intro memorables from previous turn
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pendulum

if TYPE_CHECKING:
    from alpha_app.chat import Chat


@dataclass
class EnrobeResult:
    """Result of enriching a user message.

    content: The enriched content blocks to send to Claude.
    events: WebSocket events to broadcast to connected clients.
             Each event is a dict with 'type' and 'data' keys.
    """
    content: list[dict]
    events: list[dict] = field(default_factory=list)


def _format_timestamp() -> str:
    """Current time in PSO-8601 format. Always local to the Pi."""
    now = pendulum.now("America/Los_Angeles")
    return now.format("ddd MMM D YYYY, h:mm A")


def _compute_approach_light(token_count: int, context_window: int) -> str:
    """Compute approach light color based on context usage.

    Green:  < 65% — plenty of room
    Yellow: 65-75% — getting full, start wrapping up
    Red:    > 75% — compaction imminent

    Auto-compact fires around 80-85%. Yellow and red must land
    BEFORE that threshold so Alpha sees the warning in time.
    """
    if context_window <= 0:
        return "green"
    ratio = token_count / context_window
    if ratio >= 0.75:
        return "red"
    elif ratio >= 0.65:
        return "yellow"
    return "green"


async def enrobe(content: list[dict], *, chat: "Chat") -> EnrobeResult:
    """Enrich a user message with context.

    Wraps the user's raw content blocks in enrichment: timestamp,
    approach lights, and (eventually) recalled memories and intro
    suggestions. Returns both the enriched content for Claude and
    a list of events for the WebSocket.

    Args:
        content: The raw user message content blocks.
        chat: The Chat instance (for token state, chat ID, etc.).

    Returns:
        EnrobeResult with enriched content and broadcast events.
    """
    events: list[dict] = []
    blocks: list[dict] = []

    # 1. Timestamp — always injected
    timestamp = _format_timestamp()
    blocks.append({"type": "text", "text": f"[{timestamp}]"})
    events.append({"type": "enrichment-timestamp", "data": timestamp})

    # 2. Approach lights — warning when context is getting full
    level = _compute_approach_light(chat.token_count, chat.context_window)
    if level != "green":
        warning = (
            "Compaction needed soon."
            if level == "red"
            else "Context window getting full."
        )
        blocks.append({"type": "text", "text": f"[Context: {level}] {warning}"})
    events.append({"type": "approach-light", "data": level})

    # 3. Memory recall (TODO)
    # memories = await recall(content, chat_id=chat.id)
    # for memory in memories:
    #     blocks.append({"type": "text", "text": f"[Memory] {memory.text}"})
    #     events.append({"type": "enrichment-memory", "data": memory.to_dict()})

    # 4. Intro memorables from previous turn (TODO)
    # memorables = get_pending_memorables(chat_id=chat.id)
    # if memorables:
    #     blocks.append({"type": "text", "text": f"## Intro speaks\n\n{memorables}"})
    #     events.append({"type": "enrichment-suggest", "data": memorables})

    # Enrichment blocks come first, then the original user message
    blocks.extend(content)

    return EnrobeResult(content=blocks, events=events)

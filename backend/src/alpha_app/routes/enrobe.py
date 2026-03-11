"""enrobe.py — Message enrichment pipeline.

The place where user messages get wrapped in memories, intro suggestions,
and timestamps before being sent to Claude.

The name: to enrobe is to coat something in chocolate. The user message
is the truffle center; everything we add is the shell.

Currently implements:
  1. Timestamp injection (PSO-8601 format)

Approach lights moved to streaming.py — they fire asynchronously
mid-turn as interjections when context thresholds are crossed,
rather than being injected at turn start when it might be too late.

TODO:
  2. Memory recall (query extraction -> search -> dedup -> format)
  3. Intro memorables from previous turn
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pendulum

from alpha_app.orientation import assemble_orientation, get_here

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


async def enrobe(content: list[dict], *, chat: "Chat") -> EnrobeResult:
    """Enrich a user message with context.

    Wraps the user's raw content blocks in enrichment: timestamp
    and (eventually) recalled memories and intro suggestions. Returns
    both the enriched content for Claude and a list of events for the
    WebSocket.

    Args:
        content: The raw user message content blocks.
        chat: The Chat instance (for token state, chat ID, etc.).

    Returns:
        EnrobeResult with enriched content and broadcast events.
    """
    events: list[dict] = []
    blocks: list[dict] = []

    # 1. Orientation — injected on first message of a new/resumed context window
    if chat._needs_orientation:
        here_str = get_here()
        orientation_blocks = assemble_orientation(here=here_str)
        blocks.extend(orientation_blocks)
        chat._needs_orientation = False

    # 2. Intro memorables from previous turn (TODO)
    # memorables = get_pending_memorables(chat_id=chat.id)
    # if memorables:
    #     blocks.append({"type": "text", "text": f"## Intro speaks\n\n{memorables}"})
    #     events.append({"type": "enrichment-suggest", "data": memorables})

    # 3. Memory recall (TODO)
    # memories = await recall(content, chat_id=chat.id)
    # for memory in memories:
    #     blocks.append({"type": "text", "text": memory.formatted})
    #     events.append({"type": "enrichment-memory", "data": memory.to_dict()})

    # 4. Timestamp — always present, just before the user message
    timestamp = _format_timestamp()
    blocks.append({"type": "text", "text": f"[Sent {timestamp}]"})
    events.append({"type": "enrichment-timestamp", "data": timestamp})

    # User message is ALWAYS the last block
    blocks.extend(content)

    return EnrobeResult(content=blocks, events=events)

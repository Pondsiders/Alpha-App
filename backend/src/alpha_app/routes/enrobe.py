"""enrobe.py — Message enrichment pipeline.

The place where user messages get wrapped in memories, intro suggestions,
and timestamps before being sent to Claude.

The name: to enrobe is to coat something in chocolate. The user message
is the truffle center; everything we add is the shell.

Implements:
  1. Orientation (full data: capsules, letter, today, here, context,
     events, todos — fetched from Postgres, Redis, and filesystem)
  2. Intro memorables from previous turn (read from chat._pending_intro)
  3. Memory recall (dual-strategy search, session dedup, formatted blocks)
  4. Timestamp injection (PSO-8601 format)

Approach lights moved to streaming.py — they fire asynchronously
mid-turn as interjections when context thresholds are crossed,
rather than being injected at turn start when it might be too late.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pendulum

from alpha_app.images import process_image_blocks
from alpha_app.memories.recall import recall_memories
from alpha_app.orientation import assemble_orientation
from alpha_app.sources import fetch_all_orientation

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

    Wraps the user's raw content blocks in enrichment: orientation,
    recalled memories, intro suggestions, and timestamps. Returns both
    the enriched content for Claude and progressive user-message events
    for the WebSocket.

    Each enrichment step emits a user-message event containing the
    COMPLETE current state of the user message (enrichment + original
    content). The frontend replaces its optimistic message each time.
    The bubble grows: text → text+timestamp → text+timestamp+memories.

    Block order:
        [orientation] → [intro] → timestamp → user message → [memories]

    Args:
        content: The raw user message content blocks.
        chat: The Chat instance (for token state, chat ID, etc.).

    Returns:
        EnrobeResult with enriched content and user-message events.
    """
    # Process images: resize to ≤1MP, compress to JPEG
    content = process_image_blocks(content)

    events: list[dict] = []
    preamble: list[dict] = []   # before user message: orientation, intro, timestamp
    postamble: list[dict] = []  # after user message: memories

    def _snapshot() -> dict:
        """Build a user-message event with the current enrichment state.

        Order for both human and Claude:
            [orientation?] [intro?] [timestamp] [user message] [memories]

        Timestamp before user text, memories after. The user's words stay
        central; enrichment frames them from both sides.
        """
        return {
            "type": "user-message",
            "data": {"content": preamble + content + postamble},
        }

    # 1. Orientation — injected on first message of a new/resumed context window
    if chat._needs_orientation:
        orientation_data = await fetch_all_orientation()
        orientation_blocks = assemble_orientation(**orientation_data)
        preamble.extend(orientation_blocks)
        chat._needs_orientation = False

    # 2. Intro memorables from previous turn
    if chat._pending_intro:
        preamble.append({"type": "text", "text": chat._pending_intro})
        chat._pending_intro = None

    # 3. Timestamp — computed instantly, broadcast immediately
    #    User story: timestamp appears basically instantly after send.
    timestamp = _format_timestamp()
    preamble.append({"type": "text", "text": f"[Sent {timestamp}]"})
    events.append(_snapshot())

    # 4. Memory recall — takes time, broadcast snapshot when complete
    #    User story: memories appear AFTER user message as "something to munch on."
    user_text = " ".join(
        b.get("text", "") for b in content if b.get("type") == "text"
    )
    if user_text.strip():
        memory_texts = await recall_memories(user_text, session_id=chat.id)
        if memory_texts:
            for mem_text in memory_texts:
                postamble.append({"type": "text", "text": mem_text})
            events.append(_snapshot())

    # Final content for Claude — same order as snapshots
    final_content = preamble + content + postamble

    return EnrobeResult(content=final_content, events=events)

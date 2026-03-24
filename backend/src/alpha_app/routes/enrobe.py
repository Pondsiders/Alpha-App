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

Returns a UserMessage domain object with two serializations:
  - to_wire()           → labeled JSON for the frontend
  - to_content_blocks() → flat block list for Claude

Approach lights moved to streaming.py — they fire asynchronously
mid-turn as interjections when context thresholds are crossed,
rather than being injected at turn start when it might be too late.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pendulum

from alpha_app.db import get_pool
from alpha_app.images import process_image_blocks
from alpha_app.memories.recall import recall_memories_rich
from alpha_app.memories.vision import process_image
from alpha_app.models import RecalledMemory, UserMessage

if TYPE_CHECKING:
    from alpha_app.chat import Chat
    from alpha_app.topics import TopicRegistry


@dataclass
class EnrobeResult:
    """Result of enriching a user message.

    message: The UserMessage domain object.
    content: The enriched content blocks to send to Claude (from message.to_content_blocks()).
    events: WebSocket events to broadcast to connected clients.
             Each event is a dict with 'type' and 'data' keys.
    """
    message: UserMessage
    content: list[dict]
    events: list[dict] = field(default_factory=list)


def _format_timestamp() -> str:
    """Current time in PSO-8601 format. Always local to the Pi."""
    now = pendulum.now("America/Los_Angeles")
    return now.format("ddd MMM D YYYY, h:mm A")


def _build_capsules(orientation_data: dict) -> list[Capsule]:
    """Extract capsules from raw orientation data."""
    capsules = []
    mapping = [
        ("yesterday", "Yesterday"),
        ("last_night", "Last night"),
        ("letter", "Letter"),
        ("today_so_far", "Today so far"),
    ]
    for key, title in mapping:
        value = orientation_data.get(key)
        if value:
            capsules.append(Capsule(key=key, title=title, content=value))
    return capsules



async def enrobe(
    content: list[dict],
    *,
    chat: "Chat",
    source: str = "human",
    msg_id: str | None = None,
    topics: list[str] | None = None,
    topic_registry: "TopicRegistry | None" = None,
) -> EnrobeResult:
    """Enrich a user message with context.

    Wraps the user's raw content blocks in enrichment: orientation,
    recalled memories, intro suggestions, and timestamps. Returns an
    EnrobeResult containing the UserMessage domain object, content blocks
    for Claude, and progressive user-message events for the WebSocket.

    Each enrichment step emits a user-message event containing the
    COMPLETE current state via UserMessage.to_wire(). The frontend
    matches by message ID and updates its state.

    Args:
        content: The raw user message content blocks.
        chat: The Chat instance (for token state, chat ID, etc.).
        source: Message source — "human", "buzzer", "intro", etc.
        msg_id: Optional message ID. Generated if not provided.

    Returns:
        EnrobeResult with UserMessage, content blocks, and events.
    """
    # Process images: resize to ≤1MP, compress to JPEG
    content = process_image_blocks(content)

    # Generate message ID
    if not msg_id:
        import uuid
        msg_id = f"msg-{uuid.uuid4().hex[:12]}"

    # Create the UserMessage domain object
    msg = UserMessage(id=msg_id, content=content, source=source)

    events: list[dict] = []

    def _snapshot() -> dict:
        """Build a user-message event with the current enrichment state."""
        return {
            "type": "user-message",
            "data": msg.to_wire(),
        }

    # 1. Orientation — now lives in the system prompt (survives --resume
    #    truncation). The _needs_orientation flag is cleared on first message
    #    so downstream code doesn't think we're still waiting.
    if chat._needs_orientation:
        chat._needs_orientation = False

    # 2. Intro memorables from previous turn
    if chat._pending_intro:
        msg.intro = chat._pending_intro
        chat._pending_intro = None

    # 3. Timestamp — computed instantly, broadcast immediately
    #    User story: timestamp appears basically instantly after send.
    msg.timestamp = _format_timestamp()
    events.append(_snapshot())

    # 4. Memory recall — takes time, broadcast snapshot when complete
    #    User story: memories appear AFTER user message as "something to munch on."
    #    Includes both text recall AND visual recall (if images present).
    user_text = " ".join(
        b.get("text", "") for b in content if b.get("type") == "text"
    )

    # 4a. Text recall
    if user_text.strip():
        rich_memories = await recall_memories_rich(user_text, session_id=chat.id)
        if rich_memories:
            for raw, formatted in rich_memories:
                msg.memories.append(RecalledMemory(
                    id=raw["id"],
                    content=raw["content"],
                    created_at=raw["created_at"],
                    score=raw["score"],
                    formatted=formatted,
                ))

    # 4b. Visual recall — process images through the vision pipeline
    #     New images → stored as memories (no results returned)
    #     Known images → matching memories returned and merged
    image_blocks = [
        b for b in content
        if b.get("type") == "image" and b.get("source", {}).get("type") == "base64"
    ]
    for img_block in image_blocks:
        try:
            import base64 as b64
            image_data = b64.b64decode(img_block["source"]["data"])
            image_memories = await process_image(
                image_data,
                source="attachment",
                db_pool=get_pool(),
            )
            for mem in image_memories:
                msg.memories.append(RecalledMemory(
                    id=mem["id"],
                    content=mem["content"],
                    created_at=mem["created_at"],
                    score=mem["score"],
                    formatted=f'## Memory #{mem["id"]} ({mem["created_at"]}, score {mem["score"]:.2f})\n{mem["content"]}',
                ))
        except Exception:
            pass  # Don't let vision failures break the message pipeline

    # Broadcast memory snapshot if any memories were found
    if msg.memories:
        events.append(_snapshot())

    # 5. Topic context — inject if requested and not already in this window
    if topics and topic_registry:
        new_topics = [t for t in topics if t not in chat._injected_topics]
        if new_topics:
            context_parts = []
            for topic_name in new_topics:
                ctx = topic_registry.get_context(topic_name)
                if ctx:
                    context_parts.append(ctx)
                    chat._injected_topics.add(topic_name)
            if context_parts:
                msg.topic_context = "\n\n---\n\n".join(context_parts)
                msg.topic_names = new_topics
                events.append(_snapshot())

    # Build final content blocks for Claude
    final_content = msg.to_content_blocks()

    return EnrobeResult(message=msg, content=final_content, events=events)

"""models.py — Domain objects for messages.

The source of truth for what a message IS. Both backend (Python) and
frontend (TypeScript) agree on the shapes defined here. Two serializations:

    to_wire()           → WebSocket JSON for the frontend (labeled fields)
    to_content_blocks() → Messages API format for Claude (positional blocks)

Same information, shaped for different consumers.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Enrichment parts — capsules and memories
# ---------------------------------------------------------------------------


@dataclass
class Capsule:
    """A temporal capsule: yesterday, last night, today so far, or letter."""

    key: str         # "yesterday", "last_night", "today", "letter"
    title: str       # "Yesterday", "Last night", "Today so far", "Letter"
    content: str     # Full text (the actual capsule content)

    def to_wire(self) -> dict:
        return {"key": self.key, "title": self.title, "content": self.content}

    def to_context(self) -> str:
        """Format for Claude — just the raw content (already has ## headers)."""
        return self.content


@dataclass
class RecalledMemory:
    """A memory surfaced by the recall pipeline."""

    id: int
    content: str       # The raw memory text
    created_at: str    # ISO timestamp
    score: float
    formatted: str     # Pre-formatted "## Memory #NNN (...)\n..." string

    def to_wire(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "score": round(self.score, 2),
            "created_at": self.created_at,
        }

    def to_context(self) -> str:
        """Format for Claude — the full ## Memory block."""
        return self.formatted


# ---------------------------------------------------------------------------
# Orientation — the full context block for first-turn messages
# ---------------------------------------------------------------------------

@dataclass
class Orientation:
    """All the context data fetched for orientation (first turn only)."""

    here: str
    capsules: list[Capsule] = field(default_factory=list)
    context_blocks: list[dict] = field(default_factory=list)  # Raw content blocks for Claude

    def to_wire(self) -> dict:
        return {
            "capsules": [c.to_wire() for c in self.capsules],
        }


# ---------------------------------------------------------------------------
# UserMessage — the domain object
# ---------------------------------------------------------------------------


@dataclass
class UserMessage:
    """A user message with all its enrichment.

    Built progressively by enrobe.py. Two outputs:
        to_wire()           → labeled JSON for the frontend
        to_content_blocks() → flat block list for Claude
    """

    id: str
    content: list[dict]                       # Raw user input (Messages API blocks)
    source: str = "human"                     # human, buzzer, intro, approach-light
    timestamp: str | None = None
    orientation: Orientation | None = None    # First turn only
    intro: str | None = None                  # Intro memorables from previous turn
    memories: list[RecalledMemory] = field(default_factory=list)
    topic_context: str | None = None          # Injected topic context
    topic_names: list[str] = field(default_factory=list)  # Which topics were injected

    @staticmethod
    def _to_display_block(block: dict) -> dict:
        """Convert a Messages API content block to frontend display format.

        Images: {type: "image", source: {type: "base64", media_type, data}}
             → {type: "image", image: "data:{media_type};base64,{data}"}
        Everything else: passed through unchanged.
        """
        if (
            block.get("type") == "image"
            and isinstance(block.get("source"), dict)
            and block["source"].get("type") == "base64"
        ):
            media_type = block["source"].get("media_type", "image/jpeg")
            data = block["source"].get("data", "")
            return {"type": "image", "image": f"data:{media_type};base64,{data}"}
        return block

    def to_wire(self) -> dict:
        """WebSocket format for the frontend. Labeled fields.

        Content blocks are converted from Messages API format to display
        format (e.g., base64 images become data URIs).
        """
        wire: dict = {
            "id": self.id,
            "source": self.source,
            "content": [self._to_display_block(b) for b in self.content],
            "timestamp": self.timestamp,
            "memories": [m.to_wire() for m in self.memories] if self.memories else None,
            "topics": self.topic_names if self.topic_names else None,
        }
        if self.orientation:
            wire["orientation"] = self.orientation.to_wire()
        return wire

    def to_content_blocks(self) -> list[dict]:
        """Messages API format for Claude. Positional block list.

        Order: [orientation] → [intro] → timestamp → user content → [memories]
        """
        blocks: list[dict] = []

        # Orientation (first turn) — all context blocks in order
        if self.orientation:
            blocks.extend(self.orientation.context_blocks)

        # Intro memorables from previous turn
        if self.intro:
            blocks.append({"type": "text", "text": self.intro})

        # Timestamp — always present once set
        if self.timestamp:
            blocks.append({"type": "text", "text": f"[Sent {self.timestamp}]"})

        # User content (the actual human input)
        blocks.extend(self.content)

        # Memories — after user content
        for mem in self.memories:
            blocks.append({"type": "text", "text": mem.to_context()})

        # Topic context — after memories
        if self.topic_context:
            blocks.append({"type": "text", "text": self.topic_context})

        return blocks

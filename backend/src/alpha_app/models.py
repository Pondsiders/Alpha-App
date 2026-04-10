"""models.py — Domain objects for messages.

The source of truth for what a message IS. Both backend (Python) and
frontend (TypeScript) agree on the shapes defined here.

UserMessage — assembled progressively by enrobe.py from user input.
    to_wire()           → labeled JSON for the frontend
    to_content_blocks() → flat block list for Claude

AssistantMessage — assembled progressively by streaming.py from Claude output.
    to_wire()           → labeled JSON for the frontend
    to_db()             → full-fidelity JSONB for app.messages

Same information, shaped for different consumers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from alpha_app.clock import pso_timestamp


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
    image_b64: str | None = None   # Quarter-MP JPEG as base64 (visual recall)

    def to_wire(self) -> dict:
        wire = {
            "id": self.id,
            "content": self.content,
            "score": round(self.score, 2),
            "created_at": self.created_at,
        }
        if self.image_b64:
            wire["image"] = f"data:image/jpeg;base64,{self.image_b64}"
        return wire

    def to_context(self) -> list[dict]:
        """Format for Claude — metadata header + image, or text memory.

        Image memories: header text block ("## Memory #NNN ...") then image block.
        Text memories: single text block with header + content.
        The header-then-image pattern makes it clear which images are recalled
        versus which were attached by the user.
        """
        if self.image_b64:
            # Image memory: metadata header + image (no caption text — the image IS the content)
            return [
                {"type": "text", "text": self.formatted.split("\n", 1)[0]},  # Just the ## Memory header line
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": self.image_b64,
                }},
            ]
        else:
            # Text memory: header + content as one text block
            return [
                {"type": "text", "text": self.formatted},
            ]


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


# Sources that put the frontend into "isRunning" mode: the composer shows a
# stop button, new sends are rejected until the turn completes. The dual of
# this set is the interruptible sources — non-blocking messages that can be
# interrupted by a new human message (reflection, approach-light, future
# solitude). Adding a new source is a one-line change here.
_BLOCKING_SOURCES: frozenset[str] = frozenset({"human", "buzzer"})


@dataclass
class UserMessage:
    """A user message with all its enrichment.

    Built progressively by enrobe.py. Two outputs:
        to_wire()           → labeled JSON for the frontend
        to_content_blocks() → flat block list for Claude
    """

    id: str
    content: list[dict]                       # Raw user input (Messages API blocks)
    source: str = "human"                     # human, buzzer, reflection, approach-light
    # Auto-stamped at creation. The lambda indirection means tests can patch
    # `alpha_app.models.pso_timestamp` and the factory will pick up the patch,
    # whereas a direct `default_factory=pso_timestamp` would capture the
    # function object at class-definition time and ignore later patches.
    timestamp: str = field(default_factory=lambda: pso_timestamp())
    orientation: Orientation | None = None    # First turn only
    intro: str | None = None                  # Intro memorables from previous turn
    memories: list[RecalledMemory] = field(default_factory=list)
    topic_context: str | None = None          # Injected topic context
    topic_names: list[str] = field(default_factory=list)  # Which topics were injected
    _dirty: bool = field(default=True, repr=False)  # Born dirty — flush writes to Postgres
    _confirmed: bool = field(default=False, repr=False)  # Pencil (False) until Claude echoes it (True)

    @property
    def blocks_input(self) -> bool:
        """Does this message block frontend input while in flight?

        True for human and buzzer (they initiate a turn the user is waiting
        on — composer shows stop button). False for reflection, approach
        lights, and other non-human sources (they run invisibly; if a new
        human message arrives mid-flight, the human wins and the in-flight
        message gets interrupted). The dual of this property is
        "interruptible" — non-blocking messages are interruptible.
        """
        return self.source in _BLOCKING_SOURCES

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

    def to_db(self) -> dict:
        """Full-fidelity format for app.messages. Same as to_wire() for user messages."""
        return self.to_wire()

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

        # Memories — after user content (may include image blocks)
        for mem in self.memories:
            blocks.extend(mem.to_context())

        # Topic context — after memories
        if self.topic_context:
            blocks.append({"type": "text", "text": self.topic_context})

        return blocks


# ---------------------------------------------------------------------------
# AssistantMessage — the domain object
# ---------------------------------------------------------------------------


@dataclass
class AssistantMessage:
    """An assistant response, assembled progressively during streaming.

    Built incrementally in streaming.py as events arrive from Claude.
    Parts accumulate as thinking, text, and tool-call blocks stream in.
    Token counts are fed in by streaming.py reading the proxy's sniffed
    values — the model doesn't know about the proxy, just holds the data.

    Two serializations:
        to_wire() → labeled JSON for the frontend (WebSocket broadcast)
        to_db()   → full-fidelity JSONB for app.messages (Postgres)

    No to_content_blocks() — Claude produces these, doesn't consume them.
    """

    id: str
    parts: list[dict] = field(default_factory=list)
    # Parts are:
    #   {"type": "thinking", "thinking": "..."}
    #   {"type": "text", "text": "..."}
    #   {"type": "tool-call", "toolCallId": "...", "toolName": "...",
    #    "args": {...}, "argsText": "..."}

    # Token accounting — accumulated during assembly by streaming.py
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    context_window: int = 0

    # Metadata — set at turn completion
    model: str | None = None
    stop_reason: str | None = None
    cost_usd: float = 0.0
    duration_ms: float = 0.0
    inference_count: int = 0
    _dirty: bool = field(default=True, repr=False)  # Born dirty — flush writes to Postgres

    def to_wire(self) -> dict:
        """WebSocket format for the frontend.

        Matches the shape the frontend already expects from the
        coalesced assistant-message event.
        """
        return {
            "id": self.id,
            "parts": self.parts,
            "tokenCount": self.input_tokens,
            "contextWindow": self.context_window,
        }

    def to_db(self) -> dict:
        """Full-fidelity format for app.messages.

        Includes everything: parts, token accounting, model info.
        Richer than the wire format — the database gets the full picture.
        """
        return {
            "id": self.id,
            "parts": self.parts,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "context_window": self.context_window,
            "model": self.model,
            "stop_reason": self.stop_reason,
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
            "inference_count": self.inference_count,
        }

    @property
    def text(self) -> str:
        """Just the text content — for the suggest pipeline."""
        return " ".join(
            p["text"] for p in self.parts
            if p.get("type") == "text" and p.get("text")
        )


# ---------------------------------------------------------------------------
# SystemMessage — endogenous input (task notifications, etc.)
# ---------------------------------------------------------------------------


@dataclass
class SystemMessage:
    """A system event rendered as a first-class message in the conversation.

    Not from the human. Not from me. From the infrastructure.
    Task notifications, reflection bookkeeping, compact boundaries —
    events that I react to as endogenous stimuli.
    """

    id: str
    text: str
    source: str = "system"  # "task_notification", "reflection", "compact", ...
    timestamp: str = field(default_factory=lambda: pso_timestamp())
    _dirty: bool = field(default=True, repr=False)  # Born dirty — flush writes to Postgres

    def to_wire(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "source": self.source,
            "timestamp": self.timestamp,
        }

    def to_db(self) -> dict:
        return self.to_wire()

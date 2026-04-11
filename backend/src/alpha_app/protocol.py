"""Wire protocol models — commands (client → server) and events (server → client).

See PROTOCOL.md for the design rationale. Key principles:
- Commands and events are different shapes (asymmetric protocol)
- Flat payloads (no nested data/metadata/params)
- Required fields are required (Pydantic explodes on missing fields)
- id means "I expect a response" (absent = fire-and-forget)
- chatId means "this belongs to a chat"
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# =============================================================================
# Commands (client → server)
# =============================================================================


class _CommandBase(BaseModel):
    """Common fields for all commands."""
    id: str | None = None  # Correlation ID. Present = expects response.
    chatId: str | None = None  # Scoped to a chat when present.


class JoinChatCommand(_CommandBase):
    """Load a chat's full history and metadata."""
    command: Literal["join-chat"]
    chatId: str  # Required — must know which chat to join.


class CreateChatCommand(_CommandBase):
    """Create a new conversation."""
    command: Literal["create-chat"]


class SendCommand(_CommandBase):
    """Send a user message to Claude."""
    command: Literal["send"]
    chatId: str  # Required.
    messageId: str | None = None  # Frontend-generated ID for reconciliation.
    content: list[dict[str, Any]]  # Messages API content blocks.


class InterruptCommand(_CommandBase):
    """Stop Claude mid-response. Fire-and-forget (no id needed)."""
    command: Literal["interrupt"]
    chatId: str  # Required.


class BuzzCommand(_CommandBase):
    """The duck button — inject a system message."""
    command: Literal["buzz"]
    chatId: str  # Required.


# Discriminated union of all commands, keyed on the `command` field.
Command = (
    JoinChatCommand
    | CreateChatCommand
    | SendCommand
    | InterruptCommand
    | BuzzCommand
)


def parse_command(raw: dict[str, Any]) -> Command:
    """Parse and validate a raw dict into a typed Command.

    Raises ValidationError if the shape is wrong or required fields are missing.
    """
    from pydantic import TypeAdapter
    adapter = TypeAdapter(Command)
    return adapter.validate_python(raw)


# =============================================================================
# Events (server → client)
# =============================================================================


class _EventBase(BaseModel):
    """Common fields for all events."""
    id: str | None = None  # Echoed from the command that triggered this.
    chatId: str | None = None  # Scoped to a chat when present.


# -- Chat lifecycle -----------------------------------------------------------

class AppStateEvent(_EventBase):
    """Global application state. Sent on connect and broadcast on changes."""
    event: Literal["app-state"]
    chats: list[dict[str, Any]]  # Full chat list for sidebar.
    solitude: bool = False  # Night mode flag.
    version: str = ""  # App version for stale-frontend detection.


class ChatLoadedEvent(_EventBase):
    """Response to join-chat. Full message history + metadata."""
    event: Literal["chat-loaded"]
    chatId: str
    title: str
    createdAt: float
    updatedAt: float
    state: str
    tokenCount: int
    contextWindow: int
    sessionUuid: str | None = None
    messages: list[dict[str, Any]]


class ChatCreatedEvent(_EventBase):
    """A new chat exists."""
    event: Literal["chat-created"]
    chatId: str
    title: str = ""
    createdAt: float


class ChatStateEvent(_EventBase):
    """A chat's state changed."""
    event: Literal["chat-state"]
    chatId: str
    state: str  # "idle", "busy", "dead"


# -- Turn lifecycle -----------------------------------------------------------

class SendAckEvent(_EventBase):
    """Response to send. Enrichment is running, Claude is about to respond."""
    event: Literal["send-ack"]
    chatId: str


class UserMessageEvent(_EventBase):
    """Enriched user message echoed back (pencil → ink)."""
    event: Literal["user-message"]
    chatId: str
    messageId: str
    content: list[dict[str, Any]]
    memories: list[dict[str, Any]] = Field(default_factory=list)
    timestamp: str = ""


class ThinkingDeltaEvent(_EventBase):
    """Fragment of Claude's extended thinking."""
    event: Literal["thinking-delta"]
    chatId: str
    delta: str


class TextDeltaEvent(_EventBase):
    """Fragment of Claude's text response."""
    event: Literal["text-delta"]
    chatId: str
    delta: str


class ToolCallStartEvent(_EventBase):
    """Claude decided to call a tool. Args still streaming."""
    event: Literal["tool-call-start"]
    chatId: str
    toolCallId: str
    name: str


class ToolCallDeltaEvent(_EventBase):
    """JSON fragment of tool call args being assembled."""
    event: Literal["tool-call-delta"]
    chatId: str
    toolCallId: str
    delta: str


class ToolCallResultEvent(_EventBase):
    """Tool finished executing."""
    event: Literal["tool-call-result"]
    chatId: str
    toolCallId: str
    name: str
    args: dict[str, Any]
    result: Any


class AssistantMessageEvent(_EventBase):
    """Complete finished assistant message."""
    event: Literal["assistant-message"]
    chatId: str
    messageId: str
    content: list[dict[str, Any]]


class TurnCompleteEvent(_EventBase):
    """Turn finished. Updated token counts."""
    event: Literal["turn-complete"]
    chatId: str
    tokenCount: int
    contextWindow: int
    percent: float


# -- Context ------------------------------------------------------------------

class ContextUpdateEvent(_EventBase):
    """Token counts changed outside a turn."""
    event: Literal["context-update"]
    chatId: str
    tokenCount: int
    contextWindow: int
    percent: float


# -- Errors -------------------------------------------------------------------

class ErrorEvent(_EventBase):
    """Something broke."""
    event: Literal["error"]
    code: str  # Domain-specific: "not-found", "invalid-state", etc.
    message: str

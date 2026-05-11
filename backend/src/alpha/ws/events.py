"""Outbound event models — what the server sends.

Each event is a Pydantic model with an `event` literal field. Models are
frozen and forbid extras.

Wire convention: the wire is camelCase; Python is snake_case. `BaseEvent`
sets `alias_generator=to_camel`, so a Python attribute `chat_id`
serializes as a wire field `chatId` automatically — provided callers
serialize with `by_alias=True`. Use explicit `Field(alias=...)` only
for irregular cases the generator can't infer.

Right now only `Error` is defined. Real events (`app-state`, `chat-loaded`,
`text-delta`, etc.) land as their handlers do.
"""

from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class BaseEvent(BaseModel):
    """Common fields and config for every outbound event."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="forbid",
        alias_generator=to_camel,
        populate_by_name=True,
    )

    id: str | None = None
    """Correlation ID. Echoed from the command that triggered this event,
    when the event is a response. Absent on unsolicited events."""


class Error(BaseEvent):
    """Domain-level error event — a valid command that couldn't be done.

    Codes are domain strings: `not-found`, `invalid-state`,
    `subprocess-died`, `context-exceeded`, etc. Wire-shape failures
    (malformed JSON, unknown commands, validation errors) are bugs that
    raise; they never reach this event.
    """

    event: Literal["error"] = "error"
    code: str
    message: str


class ChatCreated(BaseEvent):
    """A new chat was created. Emitted in response to `create-chat`."""

    event: Literal["chat-created"] = "chat-created"
    chat_id: str
    created_at: datetime
    last_active: datetime
    archived: bool


ChatStateValue = Literal[
    "pending", "ready", "preprocessing", "processing", "postprocessing"
]
"""Position of a chat in the turn lifecycle. See `Chat` for the full state
machine — states, transitions, and the composer-input rule."""


class ChatSummary(BaseModel):
    """One chat's summary fields, as carried in `app-state.chats`."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="forbid",
        alias_generator=to_camel,
        populate_by_name=True,
    )

    chat_id: str
    created_at: datetime
    last_active: datetime
    state: ChatStateValue
    token_count: int
    context_window: int


class AppState(BaseEvent):
    """Global application state. Pushed unsolicited on every WebSocket connect."""

    event: Literal["app-state"] = "app-state"
    chats: list[ChatSummary]
    version: str


class ChatState(BaseEvent):
    """A chat's runtime state changed.

    Single source of truth for the context meter and the turn-lifecycle
    state. Sent whenever any of the carried values change.
    """

    event: Literal["chat-state"] = "chat-state"
    chat_id: str
    state: ChatStateValue
    token_count: int
    context_window: int
    percent: float


class AssistantMessage(BaseEvent):
    """The complete, finished assistant message for a turn.

    Carries Anthropic-shaped content blocks (`text`, `thinking`, `tool-use`,
    `tool-result`). Sent at the end of a turn, after any streaming deltas.
    The frontend uses this to finalize whichever placeholder the deltas were
    accumulating into — see `useAlphaWebSocket.ts` for the seal logic.
    """

    event: Literal["assistant-message"] = "assistant-message"
    chat_id: str
    content: list[dict[str, Any]]

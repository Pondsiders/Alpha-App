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
from typing import ClassVar, Literal

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

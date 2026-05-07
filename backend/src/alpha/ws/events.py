"""Outbound event models — what the server sends.

Each event is a Pydantic model with an `event` literal field. Models are
frozen and forbid extras; we serialize them with `by_alias=True` so wire
field names like `chatId` come out camelCase.

Right now only `Error` is defined. Real events (`app-state`, `chat-loaded`,
`text-delta`, etc.) land as their handlers do.
"""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict


class BaseEvent(BaseModel):
    """Common fields and config for every outbound event."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
    )

    id: str | None = None
    """Correlation ID. Echoed from the command that triggered this event,
    when the event is a response. Absent on unsolicited events."""


class Error(BaseEvent):
    """Protocol-level or domain-level error event.

    Codes are domain strings: `invalid-json`, `invalid-command`,
    `unknown-command`, `validation-failed`, `not-found`, etc.
    """

    event: Literal["error"] = "error"
    code: str
    message: str

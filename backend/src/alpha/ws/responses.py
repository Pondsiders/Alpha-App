"""Outbound response models — what the server sends to one client in reply to a command.

Each response is a Pydantic model with a `response` literal field. Models
are frozen and forbid extras. Every response carries `id`, echoed from the
originating command.

Wire convention: the wire is camelCase; Python is snake_case. `BaseResponse`
sets `alias_generator=to_camel`, so a Python attribute `chat_id` serializes
as a wire field `chatId` automatically — provided callers serialize with
`by_alias=True`. Use explicit `Field(alias=...)` only for irregular cases
the generator can't infer.
"""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from alpha.ws.events import ChatSummary


class BaseResponse(BaseModel):
    """Common fields and config for every outbound response."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="forbid",
        alias_generator=to_camel,
        populate_by_name=True,
    )

    id: str
    """Correlation ID. Echoed from the originating command."""


class HiYourself(BaseResponse):
    """Current global state, sent in reply to `hello`."""

    response: Literal["hi-yourself"] = "hi-yourself"
    chats: list[ChatSummary]
    version: str


class ChatCreated(BaseResponse):
    """Acknowledges a `create-chat` command and returns the new chat's id."""

    response: Literal["chat-created"] = "chat-created"
    chat_id: str


class Received(BaseResponse):
    """Acknowledges a `send` command."""

    response: Literal["received"] = "received"


class Interrupted(BaseResponse):
    """Acknowledges an `interrupt` command."""

    response: Literal["interrupted"] = "interrupted"

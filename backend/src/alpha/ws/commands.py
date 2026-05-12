"""Inbound command models — what the client sends.

Each command is a Pydantic model with a `command` literal field. Together
they form a discriminated union, so a single `Command` adapter parses any
inbound message and returns the right concrete subclass.

Models are frozen and forbid extras. The wire protocol is strict; payloads
that don't match are protocol violations, not warnings.

Wire convention: the wire is camelCase; Python is snake_case. `BaseCommand`
sets `alias_generator=to_camel`, so a Python attribute `chat_id` parses
from a wire field `chatId` automatically. Use explicit `Field(alias=...)`
only for irregular cases the generator can't infer.

Add a new command:
1. Define a class extending `BaseCommand` with a Literal `command` field.
2. Append it to the `Command` union below.
3. Register a handler in `alpha.ws.handlers`.
"""

from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter
from pydantic.alias_generators import to_camel

MessageId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{21}$")]
"""A 21-character nanoid using the default URL-safe alphabet. Same shape
as `ChatId`; the wire field name carries the role, the value's shape
just says 'nanoid.'"""


class BaseCommand(BaseModel):
    """Common fields and config for every inbound command."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="forbid",
        alias_generator=to_camel,
        populate_by_name=True,
    )

    id: str | None = None
    """Correlation ID. Echoed on the response when a command expects one."""


class Hello(BaseCommand):
    """Open a session. Sent by the client immediately after the WebSocket connects."""

    command: Literal["hello"]


class JoinChat(BaseCommand):
    """Load a chat's full history and metadata."""

    command: Literal["join-chat"]
    chat_id: str


class CreateChat(BaseCommand):
    """Create a new chat. The server replies with `chat-created`."""

    command: Literal["create-chat"]


class Send(BaseCommand):
    """Send a user message to a chat."""

    command: Literal["send"]
    chat_id: str
    message_id: MessageId
    """Frontend-minted correlation token for the user message. The backend
    stamps this onto the broadcast `user-message` echo so the originating
    client can find its optimistic placeholder. Other clients see the same
    field and ignore it (they have no local placeholder to match). The ID
    is in-flight only — persistent message IDs are owned by the SDK's
    session transcript, not by us."""
    content: list[dict[str, Any]]
    """Anthropic-shaped content blocks. Validation of the inner shape is
    deferred to whatever consumes the content; the wire layer only needs to
    know it's a list of dicts."""


class Interrupt(BaseCommand):
    """Interrupt the assistant mid-turn."""

    command: Literal["interrupt"]
    chat_id: str


Command = Annotated[
    Hello | JoinChat | CreateChat | Send | Interrupt,
    Field(discriminator="command"),
]
"""Discriminated union of every inbound command shape."""

CommandAdapter: TypeAdapter[Command] = TypeAdapter(Command)
"""Validates a raw dict against the union and returns the right subclass.
Raises pydantic.ValidationError on any deviation."""

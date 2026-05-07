"""Inbound command models — what the client sends.

Each command is a Pydantic model with a `command` literal field. Together
they form a discriminated union, so a single `Command` adapter parses any
inbound message and returns the right concrete subclass.

Models are frozen and forbid extras. The wire protocol is strict; payloads
that don't match are protocol violations, not warnings.

Add a new command:
1. Define a class extending `BaseCommand` with a Literal `command` field.
2. Append it to the `Command` union below.
3. Register a handler in `alpha.ws.handlers`.
"""

from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class BaseCommand(BaseModel):
    """Common fields and config for every inbound command."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    id: str | None = None
    """Correlation ID. Echoed on the response event when a command expects one."""


class JoinChat(BaseCommand):
    """Load a chat's full history and metadata."""

    command: Literal["join-chat"]
    chat_id: str = Field(alias="chatId")


class CreateChat(BaseCommand):
    """Create a new chat. The server replies with `chat-created`."""

    command: Literal["create-chat"]


class Send(BaseCommand):
    """Send a user message to a chat."""

    command: Literal["send"]
    chat_id: str = Field(alias="chatId")
    content: list[dict[str, Any]]
    """Anthropic-shaped content blocks. Validation of the inner shape is
    deferred to whatever consumes the content; the wire layer only needs to
    know it's a list of dicts."""


class Interrupt(BaseCommand):
    """Interrupt the assistant mid-turn."""

    command: Literal["interrupt"]
    chat_id: str = Field(alias="chatId")


Command = Annotated[
    JoinChat | CreateChat | Send | Interrupt,
    Field(discriminator="command"),
]
"""Discriminated union of every inbound command shape."""

CommandAdapter: TypeAdapter[Command] = TypeAdapter(Command)
"""Validates a raw dict against the union and returns the right subclass.
Raises pydantic.ValidationError on any deviation."""

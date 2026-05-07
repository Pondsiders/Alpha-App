"""Command dispatcher — routes a validated command to its handler.

A `Dispatcher` owns the mapping from command-class to handler function.
The websocket router validates inbound messages via `CommandAdapter`
before they reach here, so dispatch sees only `BaseCommand` instances.
Validation failures are bugs, not protocol cases — they propagate as
exceptions and FastAPI closes the socket. See `wire-protocol.md`.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import logfire
from fastapi import WebSocket

from alpha.ws.commands import (
    BaseCommand,
    CreateChat,
    Interrupt,
    JoinChat,
    Send,
)
from alpha.ws.handlers import create_chat, interrupt, join_chat, send

# Each handler accepts a concrete BaseCommand subclass. Function-parameter
# types are contravariant in Python, so we can't tighten this to
# `Callable[[BaseCommand, ...]]` without losing the per-handler narrowing.
# `Any` is the right type here; the dispatcher's dict guarantees we only
# call a handler with the exact subclass it was registered against.
Handler = Callable[[Any, WebSocket], Awaitable[None]]


class Dispatcher:
    """Routes inbound commands to registered handlers."""

    def __init__(self) -> None:
        """Build the command-to-handler map.

        One Dispatcher per WebSocket connection. The handler map is fixed at
        construction; new commands require a code change, not runtime registration.
        """
        self._handlers: dict[type[BaseCommand], Handler] = {
            JoinChat: join_chat.handle,
            CreateChat: create_chat.handle,
            Send: send.handle,
            Interrupt: interrupt.handle,
        }

    async def dispatch(self, websocket: WebSocket, command: BaseCommand) -> None:
        """Run the handler matching the command's concrete type."""
        with logfire.span(
            "ws.command",
            command=command.__class__.__name__,
            request_id=command.id,
        ):
            handler = self._handlers[type(command)]
            await handler(command, websocket)

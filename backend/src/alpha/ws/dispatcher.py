"""Command dispatcher — parses, validates, routes one inbound message.

A `Dispatcher` owns the mapping from command-name to handler function.
The websocket router builds one and hands every parsed JSON message to
`dispatch(websocket, msg)`. Structural errors (bad JSON, missing
`command` field, unknown command, validation failure) are emitted as
`error` events directly. Successful parses are forwarded to the
registered handler.

Handlers are async functions of shape `(command, websocket) -> None`
where `command` is a concrete `BaseCommand` subclass. Handlers may
raise; `NotImplementedError` is the explicit "not yet" sentinel.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import logfire
from fastapi import WebSocket
from pydantic import ValidationError

from alpha.ws.commands import (
    BaseCommand,
    CommandAdapter,
    CreateChat,
    Interrupt,
    JoinChat,
    Send,
)
from alpha.ws.events import Error
from alpha.ws.handlers import create_chat, interrupt, join_chat, send

Handler = Callable[[Any, WebSocket], Awaitable[None]]


class Dispatcher:
    """Routes inbound commands to registered handlers."""

    def __init__(self) -> None:
        self._handlers: dict[type[BaseCommand], Handler] = {
            JoinChat: join_chat.handle,
            CreateChat: create_chat.handle,
            Send: send.handle,
            Interrupt: interrupt.handle,
        }

    async def dispatch(self, websocket: WebSocket, msg: dict[str, Any]) -> None:
        """Parse one message, validate it, and run the matching handler."""
        command_name = msg.get("command")
        request_id = msg.get("id") if isinstance(msg.get("id"), str) else None

        with logfire.span(
            "ws.command",
            command=command_name,
            request_id=request_id,
        ):
            if not isinstance(command_name, str):
                await _emit(websocket, Error(
                    code="invalid-command",
                    message="Message had no `command` field.",
                    id=request_id,
                ))
                return

            try:
                command = CommandAdapter.validate_python(msg)
            except ValidationError as e:
                # The discriminator's "no match" error reads as
                # `unknown-command`; everything else is a payload-shape
                # mismatch (`validation-failed`).
                if _is_unknown_command(e):
                    await _emit(websocket, Error(
                        code="unknown-command",
                        message=f"Unknown command: {command_name!r}.",
                        id=request_id,
                    ))
                else:
                    await _emit(websocket, Error(
                        code="validation-failed",
                        message=_summarize(e),
                        id=request_id,
                    ))
                return

            handler = self._handlers[type(command)]
            await handler(command, websocket)


def _is_unknown_command(e: ValidationError) -> bool:
    """A discriminator mismatch surfaces as a `union_tag_invalid` error."""
    return any(err["type"] == "union_tag_invalid" for err in e.errors())


def _summarize(e: ValidationError) -> str:
    """One-line summary of a validation failure for the wire response."""
    first = e.errors()[0]
    loc = ".".join(str(p) for p in first["loc"]) or "<root>"
    return f"{loc}: {first['msg']}"


async def _emit(websocket: WebSocket, event: Error) -> None:
    """Serialize and send one event."""
    await websocket.send_json(event.model_dump(by_alias=True, exclude_none=True))

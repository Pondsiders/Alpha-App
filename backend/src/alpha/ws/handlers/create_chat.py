"""Handle the `create-chat` command."""

# Stub handler. Pyright fuck off until implementation lands.
# pyright: reportUnusedParameter=false

from fastapi import WebSocket

from alpha.ws.commands import CreateChat


async def handle(command: CreateChat, websocket: WebSocket) -> None:
    """Create a new chat and reply with `chat-created`."""
    raise NotImplementedError("create-chat")

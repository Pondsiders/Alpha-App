"""Handle the `join-chat` command."""

# Stub handler. Pyright fuck off until implementation lands.
# pyright: reportUnusedParameter=false

from fastapi import WebSocket

from alpha.ws.commands import JoinChat


async def handle(command: JoinChat, websocket: WebSocket) -> None:
    """Load chat history and reply with `chat-loaded`."""
    raise NotImplementedError("join-chat")

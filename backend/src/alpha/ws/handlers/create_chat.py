"""Handle the `create-chat` command."""

from fastapi import WebSocket

from alpha.ws.commands import CreateChat


async def handle(command: CreateChat, websocket: WebSocket) -> None:
    """Create a new chat and reply with `chat-created`."""
    raise NotImplementedError("create-chat")

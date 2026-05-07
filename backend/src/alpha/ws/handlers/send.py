"""Handle the `send` command."""

# Stub handler. Pyright fuck off until implementation lands.
# pyright: reportUnusedParameter=false

from fastapi import WebSocket

from alpha.ws.commands import Send


async def handle(command: Send, websocket: WebSocket) -> None:
    """Send a user message into a chat and stream the assistant's reply."""
    raise NotImplementedError("send")

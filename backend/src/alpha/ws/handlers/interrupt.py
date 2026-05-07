"""Handle the `interrupt` command."""

# Stub handler. Pyright fuck off until implementation lands.
# pyright: reportUnusedParameter=false

from fastapi import WebSocket

from alpha.ws.commands import Interrupt


async def handle(command: Interrupt, websocket: WebSocket) -> None:
    """Interrupt the assistant mid-turn."""
    raise NotImplementedError("interrupt")

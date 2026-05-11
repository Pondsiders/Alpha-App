"""Handle the `send` command."""

from fastapi import WebSocket

from alpha.ws.commands import Send
from alpha.ws.events import AssistantMessage


async def handle(command: Send, websocket: WebSocket) -> None:
    """Reply to every send with a synthetic assistant-message.

    First bite of the pizza dough: prove the round-trip works end-to-end —
    composer → wire → backend → wire → thread. No SDK, no preprocessing,
    no streaming, no state transitions yet. Just one canned event.
    """
    event = AssistantMessage(
        chat_id=command.chat_id,
        content=[{"type": "text", "text": "Rubber baby buggy bumpers."}],
    )
    await websocket.send_json(
        event.model_dump(by_alias=True, exclude_none=True, mode="json")
    )

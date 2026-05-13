"""Handle the `hello` command."""

from fastapi import WebSocket

from alpha import app_state
from alpha.ws.commands import Hello
from alpha.ws.responses import HiYourself


async def handle(command: Hello, websocket: WebSocket) -> None:
    """Reply with `hi-yourself` carrying the current chat list and version."""
    version, summaries = await app_state.snapshot()
    response = HiYourself(id=command.id, chats=summaries, version=version)
    await websocket.send_json(
        response.model_dump(by_alias=True, exclude_none=True, mode="json")
    )

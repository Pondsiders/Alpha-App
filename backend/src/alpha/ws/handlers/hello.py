"""Handle the `hello` command."""

from fastapi import WebSocket

import alpha
from alpha import chats
from alpha.ws.commands import Hello
from alpha.ws.responses import HiYourself


async def handle(command: Hello, websocket: WebSocket) -> None:
    """Reply with `hi-yourself` carrying the current chat list and version."""
    summaries = [chats.summary_of(chat) for chat in await chats.all()]
    response = HiYourself(
        id=command.id,
        chats=summaries,
        version=alpha.__version__,
    )
    await websocket.send_json(
        response.model_dump(by_alias=True, exclude_none=True, mode="json")
    )

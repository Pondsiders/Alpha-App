"""Handle the `hello` command."""

from fastapi import WebSocket

import alpha
from alpha import chats
from alpha.chat import Chat
from alpha.ws.commands import Hello
from alpha.ws.events import ChatSummary
from alpha.ws.responses import HiYourself


def _summarize(chat: Chat) -> ChatSummary:
    """Project a Chat into the ChatSummary the wire carries."""
    return ChatSummary(
        chat_id=chat.chat_id,
        created_at=chat.created_at,
        last_active=chat.last_active,
        state="pending",
        token_count=0,
        context_window=1_000_000,
    )


async def handle(command: Hello, websocket: WebSocket) -> None:
    """Reply with `hi-yourself` carrying the current chat list and version."""
    if command.id is None:
        raise ValueError("hello must carry an id; it expects a response")
    summaries = [_summarize(chat) for chat in await chats.all()]
    response = HiYourself(
        id=command.id,
        chats=summaries,
        version=alpha.__version__,
    )
    await websocket.send_json(
        response.model_dump(by_alias=True, exclude_none=True, mode="json")
    )

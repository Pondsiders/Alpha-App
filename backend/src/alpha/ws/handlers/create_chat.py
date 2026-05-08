"""Handle the `create-chat` command."""

from fastapi import WebSocket

from alpha import chats
from alpha.ws.commands import CreateChat
from alpha.ws.events import ChatCreated


async def handle(command: CreateChat, websocket: WebSocket) -> None:
    """Create a new chat and reply with `chat-created`."""
    chat = await chats.create()
    event = ChatCreated(
        id=command.id,
        chat_id=chat.chat_id,
        created_at=chat.created_at,
        last_active=chat.last_active,
        archived=chat.archived,
    )
    await websocket.send_json(
        event.model_dump(by_alias=True, exclude_none=True, mode="json")
    )

"""Handle the `create-chat` command."""

from fastapi import WebSocket

from alpha import chats
from alpha.ws.commands import CreateChat
from alpha.ws.responses import ChatCreated


async def handle(command: CreateChat, websocket: WebSocket) -> None:
    """Create a new chat and reply with `chat-created`."""
    chat = await chats.create()
    response = ChatCreated(id=command.id, chat_id=chat.chat_id)
    await websocket.send_json(
        response.model_dump(by_alias=True, exclude_none=True, mode="json")
    )

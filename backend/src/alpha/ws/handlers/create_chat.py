"""Handle the `create-chat` command."""

from fastapi import WebSocket

import alpha.ws.connections as connections
from alpha import app_state, chats
from alpha.ws.commands import CreateChat
from alpha.ws.events import AppState
from alpha.ws.responses import ChatCreated


async def handle(command: CreateChat, websocket: WebSocket) -> None:
    """Create a new chat, reply with `chat-created`, broadcast `app-state`."""
    chat = await chats.create()
    response = ChatCreated(id=command.id, chat_id=chat.chat_id)
    await websocket.send_json(
        response.model_dump(by_alias=True, exclude_none=True, mode="json")
    )
    version, summaries = await app_state.snapshot()
    await connections.broadcast(AppState(chats=summaries, version=version))

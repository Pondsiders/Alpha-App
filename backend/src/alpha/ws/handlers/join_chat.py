"""Handle the `join-chat` command."""

import nanoid
from fastapi import WebSocket

from alpha import chats
from alpha.ws.commands import JoinChat
from alpha.ws.responses import ChatJoined


async def handle(command: JoinChat, websocket: WebSocket) -> None:
    """Reply with `chat-joined` carrying chat metadata and canned messages."""
    chat = await chats.get(command.chat_id)
    if chat is None:
        raise LookupError(f"chat {command.chat_id} not found")
    response = ChatJoined(
        id=command.id,
        chat_id=chat.chat_id,
        created_at=chat.created_at,
        last_active=chat.last_active,
        state="pending",
        token_count=0,
        context_window=1_000_000,
        messages=[
            {
                "role": "assistant",
                "data": {
                    "id": nanoid.generate(),
                    "parts": [{"type": "text", "text": "Rubber baby buggy bumpers."}],
                    "sealed": True,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_tokens": 0,
                    "cache_read_tokens": 0,
                    "context_window": 1_000_000,
                    "model": None,
                    "stop_reason": None,
                    "cost_usd": 0,
                    "duration_ms": 0,
                    "inference_count": 0,
                },
            },
        ],
    )
    await websocket.send_json(
        response.model_dump(by_alias=True, exclude_none=True, mode="json")
    )

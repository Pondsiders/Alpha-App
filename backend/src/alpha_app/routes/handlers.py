"""handlers.py — WebSocket message handlers for chat management.

Handles create-chat, list-chats, and interrupt messages.
Each handler receives the shared state it needs as arguments.
"""

import asyncio

from fastapi import WebSocket

from alpha_app.chat import Chat, ConversationState, generate_chat_id
from alpha_app.db import list_chats, persist_chat
from alpha_app.routes.broadcast import broadcast


async def handle_create_chat(
    ws: WebSocket,
    connections: set,
    chats: dict[str, Chat],
    on_reap,
    on_broadcast=None,
) -> None:
    """Handle create-chat: born COLD, persist, broadcast to all."""
    try:
        chat_id = generate_chat_id()
        chat = Chat(id=chat_id)
        chat.on_reap = on_reap
        chat.on_broadcast = on_broadcast
        chat._topic_registry = getattr(ws.app.state, "topic_registry", None)
        chats[chat_id] = chat

        await persist_chat(chat)

        # Broadcast to all — the requester navigates (createPendingRef),
        # other tabs just add it to their sidebar.
        await broadcast(connections, {
            "event": "chat-created",
            "chatId": chat_id,
            "title": "",
            "createdAt": chat.created_at,
        })

    except Exception as e:
        await ws.send_json({"event": "error", "data": f"Failed to create chat: {e}"})


async def handle_list_chats(
    ws: WebSocket,
    chats: dict[str, Chat],
) -> None:
    """Handle list-chats: unicast — only the requester needs the full list."""
    chat_list = await list_chats()

    for item in chat_list:
        live_chat = chats.get(item["chatId"])
        if live_chat:
            item["state"] = live_chat.state.wire_value
            item["title"] = live_chat.title or item["title"]
            item["sessionUuid"] = live_chat.session_uuid or item.get("sessionUuid", "")
            item["tokenCount"] = live_chat.token_count
            item["contextWindow"] = live_chat.context_window

    await ws.send_json({"event": "chat-list", "data": chat_list})


async def handle_interrupt(
    ws: WebSocket,
    connections: set,
    chats: dict[str, Chat],
    chat_id: str,
    streaming_tasks: dict[str, asyncio.Task],
) -> None:
    """Handle interrupt: kill the subprocess, broadcast state change."""
    if not chat_id:
        await ws.send_json({"event": "error", "data": "Missing chatId"})
        return

    chat = chats.get(chat_id)
    if chat:
        try:
            await chat.interrupt()
            await broadcast(connections, {
                "event": "chat-state",
                "chatId": chat_id,
                "state": chat.state.wire_value,
            })
        except Exception as e:
            await broadcast(connections, {"event": "error", "chatId": chat_id, "data": str(e)})

    task = streaming_tasks.get(chat_id)
    if task and not task.done():
        task.cancel()

    await broadcast(connections, {"event": "interrupted", "chatId": chat_id})

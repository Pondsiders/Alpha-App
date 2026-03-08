"""WebSocket route — bidirectional multi-chat switch.

Phase 3: The Switch — all events broadcast to all connected clients.

Every WebSocket connection registers in app.state.connections (a set).
Streaming events, state changes, and user message echoes broadcast to all
connections. Request/response messages (list-chats) unicast to the requester.
The reap timer broadcasts DEAD state via on_reap callback.

Two tabs = two connections = same conversation, synced.

Client -> Server messages:
  { "type": "create-chat" }
  { "type": "list-chats" }
  { "type": "send", "chatId": "...", "content": "Hello" }
  { "type": "send", "chatId": "...", "content": [{ "type": "text", "text": "Hello" }, ...] }
  { "type": "buzz", "chatId": "..." }  -- nonverbal hello, Alpha talks first
  { "type": "interrupt", "chatId": "..." }

Server -> Client messages (unicast — response to requester only):
  { "type": "chat-list", "data": [...] }

Server -> Client messages (broadcast — all connections):
  { "type": "chat-created", "chatId": "...", "data": { "state": "idle" } }
  { "type": "chat-state", "chatId": "...", "data": { "state": "busy", "title": "...", ... } }
  { "type": "user-message", "chatId": "...", "data": { "content": [...] } }
  { "type": "text-delta", "chatId": "...", "data": "chunk" }
  { "type": "thinking-delta", "chatId": "...", "data": "chunk" }
  { "type": "tool-call", "chatId": "...", "data": { "toolCallId", "toolName", "args", "argsText" } }
  { "type": "done", "chatId": "..." }
  { "type": "interrupted", "chatId": "..." }
  { "type": "context-update", "chatId": "...", "data": { "tokenCount": 12345, "tokenLimit": 200000 } }
  { "type": "error", "chatId": "...", "data": "something broke" }
"""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from alpha_app.chat import Chat, ConversationState, Holster
from alpha_app.db import load_chat
from alpha_app.routes.broadcast import broadcast
from alpha_app.routes.handlers import handle_create_chat, handle_interrupt, handle_list_chats
from alpha_app.routes.turn import handle_interjection, handle_new_turn
from alpha_app.strings import BUZZ_NARRATION

router = APIRouter()


def _normalize_content(raw_content: str | list) -> list[dict]:
    """Normalize raw content to Messages API content blocks."""
    if isinstance(raw_content, str):
        return [{"type": "text", "text": raw_content}]
    elif isinstance(raw_content, list):
        return raw_content
    else:
        return [{"type": "text", "text": str(raw_content)}]


@router.websocket("/ws")
async def websocket_chat(ws: WebSocket) -> None:
    """Bidirectional multi-chat switch.

    Full duplex: the read loop stays hot while streaming tasks run in the
    background. All events broadcast to all connected clients. Sends to a
    BUSY chat become interjections. User messages echo to other connections.

    Two tabs, same conversation, fully synced.
    """
    await ws.accept()

    # Register in the connection set (the switch fabric)
    connections: set = ws.app.state.connections
    connections.add(ws)

    holster: Holster = ws.app.state.holster
    chats: dict[str, Chat] = ws.app.state.chats

    # Reap callback — when a chat's idle timer fires, broadcast DEAD to all.
    async def on_chat_reap(chat_id: str) -> None:
        await broadcast(connections, {
            "type": "chat-state",
            "chatId": chat_id,
            "data": {"state": "dead"},
        })

    # Per-connection tracking for background streaming tasks
    streaming_tasks: dict[str, asyncio.Task] = {}
    turn_input_messages: dict[str, list] = {}

    try:
        while True:
            raw = await ws.receive_json()
            msg_type = raw.get("type")

            if msg_type == "create-chat":
                await handle_create_chat(ws, connections, holster, chats, on_chat_reap)

            elif msg_type == "list-chats":
                await handle_list_chats(ws, chats)

            elif msg_type == "send":
                chat_id = raw.get("chatId", "")
                raw_content = raw.get("content", "")

                if not chat_id:
                    await ws.send_json({"type": "error", "data": "Missing chatId"})
                    continue

                content = _normalize_content(raw_content)

                # Find or load the chat
                chat = chats.get(chat_id)
                if not chat:
                    chat = await load_chat(chat_id)
                    if chat:
                        chat.on_reap = on_chat_reap
                        chats[chat_id] = chat
                    else:
                        await ws.send_json({"type": "error", "chatId": chat_id, "data": "Chat not found"})
                        await ws.send_json({"type": "done", "chatId": chat_id})
                        continue

                if chat.state in (ConversationState.ENRICHING, ConversationState.RESPONDING):
                    # Interjection — feed to subprocess, echo to others
                    await handle_interjection(ws, connections, chat, content, turn_input_messages)
                else:
                    # Echo user message to other connections
                    await broadcast(connections, {
                        "type": "user-message",
                        "chatId": chat_id,
                        "data": {"content": content},
                    }, exclude=ws)

                    # New turn — start streaming in background
                    task = asyncio.create_task(
                        handle_new_turn(ws, connections, chat, content, turn_input_messages, streaming_tasks)
                    )
                    streaming_tasks[chat_id] = task

            elif msg_type == "buzz":
                # The nonverbal hello. No user message — just a stage direction
                # that Alpha sees and the human doesn't.
                chat_id = raw.get("chatId", "")

                if not chat_id:
                    await ws.send_json({"type": "error", "data": "Missing chatId"})
                    continue

                chat = chats.get(chat_id)
                if not chat:
                    chat = await load_chat(chat_id)
                    if chat:
                        chat.on_reap = on_chat_reap
                        chats[chat_id] = chat
                    else:
                        await ws.send_json({"type": "error", "chatId": chat_id, "data": "Chat not found"})
                        await ws.send_json({"type": "done", "chatId": chat_id})
                        continue

                # Narration message — stage direction, not a human message
                narration = [{"type": "text", "text": BUZZ_NARRATION}]

                # No user-message echo — the narration is invisible to the human.
                # Go straight to turn, same as send but without the broadcast.
                task = asyncio.create_task(
                    handle_new_turn(ws, connections, chat, narration, turn_input_messages, streaming_tasks)
                )
                streaming_tasks[chat_id] = task

            elif msg_type == "interrupt":
                chat_id = raw.get("chatId", "")
                await handle_interrupt(ws, connections, chats, chat_id, streaming_tasks)

            else:
                pass

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        # Deregister from the connection set
        connections.discard(ws)

        # Cancel all streaming tasks on disconnect
        for task in streaming_tasks.values():
            if not task.done():
                task.cancel()
        for task in list(streaming_tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

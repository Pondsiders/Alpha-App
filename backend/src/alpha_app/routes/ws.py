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
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from alpha_app import AssistantEvent, UserEvent, replay_session

from alpha_app.chat import Chat, ConversationState, Holster
from alpha_app.db import get_pool, load_chat
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

            elif msg_type == "replay":
                chat_id = raw.get("chatId")
                if not chat_id:
                    await ws.send_json({"type": "error", "data": "Missing chatId"})
                    continue

                # Look up session UUID from Postgres
                try:
                    pool = get_pool()
                    row = await pool.fetchrow(
                        "SELECT data FROM app.chats WHERE id = $1",
                        chat_id,
                    )
                except Exception:
                    await ws.send_json({"type": "error", "chatId": chat_id, "data": "Database error"})
                    continue

                session_uuid = None
                if row:
                    session_uuid = row["data"].get("session_uuid")

                if not session_uuid:
                    # No history — just signal done
                    await ws.send_json({"type": "replay-done", "chatId": chat_id})
                    continue

                # Find the sessions directory (same as sessions.py uses)
                from alpha_app.routes.sessions import SESSIONS_DIR

                try:
                    async for event in replay_session(session_uuid, sessions_dir=SESSIONS_DIR):
                        if isinstance(event, UserEvent):
                            # Transform user content for frontend
                            user_content = []
                            raw_content = event.content
                            for block in raw_content:
                                if isinstance(block, str):
                                    user_content.append({"type": "text", "text": block})
                                elif isinstance(block, dict):
                                    block_type = block.get("type")
                                    if block_type == "text":
                                        user_content.append({"type": "text", "text": block.get("text", "")})
                                    elif block_type == "image":
                                        source = block.get("source", {})
                                        if source.get("type") == "base64" and source.get("data"):
                                            media_type = source.get("media_type", "image/png")
                                            data_uri = f"data:{media_type};base64,{source['data']}"
                                            user_content.append({"type": "image", "image": data_uri})
                            if user_content:
                                await ws.send_json({
                                    "type": "user-message",
                                    "chatId": chat_id,
                                    "data": {"content": user_content},
                                })

                        elif isinstance(event, AssistantEvent):
                            for block in event.content:
                                if not isinstance(block, dict):
                                    continue
                                block_type = block.get("type")
                                if block_type == "text":
                                    text = block.get("text", "")
                                    if text:
                                        await ws.send_json({
                                            "type": "text-delta",
                                            "chatId": chat_id,
                                            "data": text,
                                        })
                                elif block_type == "thinking":
                                    thinking = block.get("thinking", "")
                                    if thinking:
                                        await ws.send_json({
                                            "type": "thinking-delta",
                                            "chatId": chat_id,
                                            "data": thinking,
                                        })
                                elif block_type == "tool_use":
                                    tool_input = block.get("input", {})
                                    await ws.send_json({
                                        "type": "tool-call",
                                        "chatId": chat_id,
                                        "data": {
                                            "toolCallId": block.get("id", ""),
                                            "toolName": block.get("name", ""),
                                            "args": tool_input,
                                            "argsText": json.dumps(tool_input),
                                        },
                                    })
                            # End of this assistant turn
                            await ws.send_json({"type": "done", "chatId": chat_id})

                except FileNotFoundError:
                    # JSONL file missing — not an error, just no history
                    pass

                await ws.send_json({"type": "replay-done", "chatId": chat_id})

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

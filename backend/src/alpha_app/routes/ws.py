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
  { "type": "chat-created", "chatId": "...", "data": { "state": "dead" } }  -- born COLD, wakes on first message
  { "type": "chat-state", "chatId": "...", "data": { "state": "busy", "title": "...", ... } }
  { "type": "user-message", "chatId": "...", "data": { "content": [...] } }
  { "type": "text-delta", "chatId": "...", "data": "chunk" }
  { "type": "thinking-delta", "chatId": "...", "data": "chunk" }
  { "type": "tool-call", "chatId": "...", "data": { "toolCallId", "toolName", "args", "argsText" } }
  { "type": "done", "chatId": "..." }
  { "type": "interrupted", "chatId": "..." }
  { "type": "context-update", "chatId": "...", "data": { "tokenCount": 12345, "tokenLimit": 1000000 } }
  { "type": "error", "chatId": "...", "data": "something broke" }
"""

import asyncio
import logfire

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from alpha_app.chat import Chat, ConversationState
from alpha_app.db import load_chat, replay_events
from alpha_app.routes.broadcast import broadcast
from alpha_app.routes.handlers import handle_create_chat, handle_interrupt, handle_list_chats
from alpha_app.routes.turn_smart import handle_send as handle_send_smart
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

    chats: dict[str, Chat] = ws.app.state.chats

    # Reap callback — when a chat's idle timer fires, broadcast DEAD to all.
    async def on_chat_reap(chat_id: str) -> None:
        await broadcast(connections, {
            "type": "chat-state",
            "chatId": chat_id,
            "data": {"state": "dead"},
        })

    # Broadcast callback for Smart Chat — events from Claude flow here.
    async def on_chat_broadcast(event: dict) -> None:
        await broadcast(connections, event)

    def _wire_chat(chat: Chat) -> None:
        """Set callbacks and registry on a Chat."""
        chat.on_reap = on_chat_reap
        chat.on_broadcast = on_chat_broadcast
        chat._topic_registry = getattr(ws.app.state, "topic_registry", None)

    # Per-connection tracking for background streaming tasks (legacy path)
    streaming_tasks: dict[str, asyncio.Task] = {}
    turn_input_messages: dict[str, list] = {}

    logfire.info("ws.connected", client=str(ws.client))

    try:
        while True:
            raw = await ws.receive_json()
            msg_type = raw.get("type")

            if msg_type == "create-chat":
                await handle_create_chat(ws, connections, chats, on_chat_reap, on_chat_broadcast)

            elif msg_type == "list-chats":
                await handle_list_chats(ws, chats)

            elif msg_type == "send":
                chat_id = raw.get("chatId", "")
                raw_content = raw.get("content", "")
                message_id = raw.get("messageId")  # Frontend-generated ID for reconciliation
                topics = raw.get("topics", [])      # Topic names to inject

                if not chat_id:
                    await ws.send_json({"type": "error", "data": "Missing chatId"})
                    continue

                content = _normalize_content(raw_content)

                # Find or load the chat
                chat = chats.get(chat_id)
                if not chat:
                    chat = await load_chat(chat_id)
                    if chat:
                        _wire_chat(chat)
                        chats[chat_id] = chat
                    else:
                        await ws.send_json({"type": "error", "chatId": chat_id, "data": "Chat not found"})
                        continue

                # Smart Chat path: send returns immediately, response flows via callback.
                # Modal UI: no interjections. If Claude is busy, the message waits
                # in Claude Code's internal queue.
                task = asyncio.create_task(
                    handle_send_smart(ws, connections, chat, content,
                                     msg_id=message_id, topics=topics)
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
                        _wire_chat(chat)
                        chats[chat_id] = chat
                    else:
                        await ws.send_json({"type": "error", "chatId": chat_id, "data": "Chat not found"})
                        continue

                # Narration message — stage direction, not a human message
                narration = [{"type": "text", "text": BUZZ_NARRATION}]

                task = asyncio.create_task(
                    handle_send_smart(ws, connections, chat, narration,
                                     broadcast_user_message=False, source="buzzer")
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

                events = await replay_events(chat_id)
                for event in events:
                    await ws.send_json(event)
                await ws.send_json({"type": "replay-done", "chatId": chat_id})

                # After replay: send fresh chat-state with topics so pills populate.
                # This fires AFTER replay-done, so isReplaying is false by the time
                # the frontend processes it.
                chat = chats.get(chat_id)
                if not chat:
                    chat = await load_chat(chat_id)
                    if chat:
                        _wire_chat(chat)
                        chats[chat_id] = chat
                if chat:
                    _wire_chat(chat)
                    await ws.send_json({
                        "type": "chat-state",
                        "chatId": chat_id,
                        "data": chat.wire_state(),
                    })

            elif msg_type == "join-chat":
                # The "gimme the fucking chat" protocol.
                # One payload: all messages + chat metadata including topics.
                # Replaces replay for frontends that support it.
                # ALSO: eager Claude warmup — if the chat is COLD and has a
                # session to resume, start the subprocess now so it's warm
                # by the time the user types.
                chat_id = raw.get("chatId")
                if not chat_id:
                    await ws.send_json({"type": "error", "data": "Missing chatId"})
                    continue

                from alpha_app.db import load_messages

                # Load or find the chat object
                chat = chats.get(chat_id)
                in_memory = chat is not None
                if not chat:
                    chat = await load_chat(chat_id)
                    if chat:
                        _wire_chat(chat)
                        chats[chat_id] = chat
                if chat:
                    _wire_chat(chat)

                logfire.debug(
                    "join-chat: {chat_id} found={found} in_memory={in_memory} "
                    "state={state} session={session}",
                    chat_id=chat_id,
                    found=chat is not None,
                    in_memory=in_memory,
                    state=chat.state.value if chat else "none",
                    session=chat.session_uuid[:12] if chat and chat.session_uuid else "none",
                )

                # Load messages: prefer in-memory (includes in-progress
                # AssistantMessage with latest deltas), fall back to Postgres.
                if in_memory and chat:
                    messages = chat.messages_to_wire()
                else:
                    messages = await load_messages(chat_id)

                # Build the payload
                metadata = chat.wire_state() if chat else {
                    "state": "dead",
                    "topics": {},
                }

                logfire.debug(
                    "join-chat: metadata tokenCount={tc} contextWindow={cw}",
                    tc=metadata.get("tokenCount"),
                    cw=metadata.get("contextWindow"),
                )

                await ws.send_json({
                    "type": "chat-data",
                    "chatId": chat_id,
                    "data": {
                        "messages": messages,
                        "metadata": metadata,
                    },
                })

                # Eager warmup: if COLD with a session, resurrect now.
                # The user is looking at this chat — warm the brain while
                # they read and type. Reap timer handles the waste case.
                warmup_eligible = (
                    chat is not None
                    and chat.state == ConversationState.COLD
                    and chat.session_uuid is not None
                )
                logfire.debug(
                    "join-chat: warmup_eligible={eligible} "
                    "(chat={has_chat}, state={state}, session={has_session})",
                    eligible=warmup_eligible,
                    has_chat=chat is not None,
                    state=chat.state.value if chat else "none",
                    has_session=bool(chat.session_uuid) if chat else False,
                )
                if warmup_eligible:
                    from alpha_app.tools import create_alpha_server

                    logfire.info(
                        "eager-warmup: resurrecting {chat_id} (session {session})",
                        chat_id=chat_id,
                        session=chat.session_uuid[:12],
                    )

                    def _clear() -> int:
                        if chat._pending_intro:
                            chat._pending_intro = None
                            return 1
                        return 0

                    topic_registry = getattr(ws.app.state, "topic_registry", None)
                    mcp_servers = {"alpha": create_alpha_server(
                        chat=chat,
                        clear_memorables=_clear,
                        topic_registry=topic_registry,
                        session_id=chat.id,
                    )}

                    try:
                        await chat.resurrect(
                            system_prompt=ws.app.state.system_prompt,
                            mcp_servers=mcp_servers,
                        )
                        logfire.info(
                            "eager-warmup: {chat_id} is now {state}",
                            chat_id=chat_id,
                            state=chat.state.value,
                        )
                        await broadcast(connections, {
                            "type": "chat-state",
                            "chatId": chat_id,
                            "data": chat.wire_state(),
                        })
                    except Exception as exc:
                        logfire.warn(
                            "eager-warmup: failed for {chat_id}: {error}",
                            chat_id=chat_id,
                            error=str(exc),
                        )
                        # Warmup is best-effort — don't crash join-chat

            else:
                pass

    except WebSocketDisconnect:
        logfire.debug("ws.disconnected", client=str(ws.client))
    except Exception as exc:
        logfire.warn("ws.error: {error}", error=str(exc))
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

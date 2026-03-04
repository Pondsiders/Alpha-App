"""WebSocket route — bidirectional multi-chat pipe.

Phase 2: All messages carry chatId. Multiple chats multiplexed over one connection.

Client -> Server messages:
  { "type": "create-chat" }
  { "type": "list-chats" }
  { "type": "send", "chatId": "...", "content": "Hello" }
  { "type": "send", "chatId": "...", "content": [{ "type": "text", "text": "Hello" }, ...] }
  { "type": "interrupt", "chatId": "..." }

Server -> Client messages:
  { "type": "chat-created", "chatId": "...", "data": { "state": "idle" } }
  { "type": "chat-list", "data": [...] }
  { "type": "chat-state", "chatId": "...", "data": { "state": "busy", "title": "...", "updatedAt": ..., "sessionUuid": "..." } }
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
import logging
import os

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from alpha_sdk import AssistantEvent, ResultEvent, ErrorEvent, StreamEvent

from alpha_app.chat import Chat, ChatState, Holster, generate_chat_id

log = logging.getLogger(__name__)

router = APIRouter()

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
CHAT_TTL = 29 * 24 * 3600  # 29 days in seconds


# -- Redis helpers ------------------------------------------------------------


async def _persist_chat(chat: Chat) -> None:
    """Persist chat metadata to Redis."""
    try:
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            pipe = r.pipeline()
            pipe.hset(f"alpha:chat:{chat.id}", mapping=chat.serialize())
            pipe.expire(f"alpha:chat:{chat.id}", CHAT_TTL)
            pipe.zadd("alpha:chats", {chat.id: chat.updated_at})
            await pipe.execute()
        finally:
            await r.aclose()
    except Exception as e:
        log.warning("Redis persist failed (non-fatal): %s", e)


async def _list_chats_from_redis() -> list[dict]:
    """Load chat list from Redis for sidebar hydration."""
    try:
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            chat_ids = await r.zrevrange("alpha:chats", 0, 99)
            result = []
            for cid in chat_ids:
                meta = await r.hgetall(f"alpha:chat:{cid}")
                if meta:
                    result.append({
                        "chatId": cid,
                        "title": meta.get("title", ""),
                        "state": "dead",  # Always dead from Redis — live overlay adds runtime state
                        "updatedAt": float(meta.get("updated_at", 0) or 0),
                        "sessionUuid": meta.get("session_uuid", ""),
                    })
            return result
        finally:
            await r.aclose()
    except Exception as e:
        log.warning("Redis list failed (non-fatal): %s", e)
        return []


async def _load_chat_from_redis(chat_id: str) -> Chat | None:
    """Load a chat's metadata from Redis. Returns a DEAD Chat, or None."""
    try:
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            data = await r.hgetall(f"alpha:chat:{chat_id}")
            if data:
                return Chat.from_redis(chat_id, data)
            return None
        finally:
            await r.aclose()
    except Exception as e:
        log.warning("Redis load failed (non-fatal): %s", e)
        return None


# -- Event streaming ----------------------------------------------------------


async def _stream_chat_events(ws: WebSocket, chat: Chat) -> None:
    """Stream events from a Chat to the WebSocket. Handles turn lifecycle.

    All emitted events carry the chatId. On turn completion, emits chat-state
    with the updated state, then done.

    Also monitors token_count changes (from the proxy's SSE sniffing) and
    emits context-update events in real time — so the browser ContextMeter
    updates live during multi-tool-call turns.
    """
    chat_id = chat.id
    turn_completed = False
    last_token_count = chat.token_count  # Track changes

    try:
        async for event in chat.events():
            # -- Real-time context updates --
            # Check after each event whether token_count changed.
            # The proxy sniffs input_tokens from each API call's message_start,
            # so during a 30-tool-call turn the count climbs. After auto-compact,
            # it drops. Either way, the browser sees it live.
            current_tokens = chat.token_count
            if current_tokens != last_token_count:
                last_token_count = current_tokens
                try:
                    await ws.send_json({
                        "type": "context-update",
                        "chatId": chat_id,
                        "data": {
                            "tokenCount": current_tokens,
                            "tokenLimit": chat.context_window,
                        },
                    })
                except Exception:
                    pass  # Don't let meter updates kill the stream

            if isinstance(event, StreamEvent):
                if event.delta_type == "text_delta":
                    text = event.delta_text
                    if text:
                        await ws.send_json({"type": "text-delta", "chatId": chat_id, "data": text})
                elif event.delta_type == "thinking_delta":
                    text = event.delta_text
                    if text:
                        await ws.send_json({"type": "thinking-delta", "chatId": chat_id, "data": text})

            elif isinstance(event, AssistantEvent):
                for block in event.content:
                    if block.get("type") == "tool_use":
                        await ws.send_json({
                            "type": "tool-call",
                            "chatId": chat_id,
                            "data": {
                                "toolCallId": block.get("id", ""),
                                "toolName": block.get("name", ""),
                                "args": block.get("input", {}),
                                "argsText": json.dumps(block.get("input", {})),
                            },
                        })

            elif isinstance(event, ResultEvent):
                # Persist updated metadata (session UUID, title, etc.)
                asyncio.create_task(_persist_chat(chat))

                # Notify state change: BUSY -> IDLE
                await ws.send_json({
                    "type": "chat-state",
                    "chatId": chat_id,
                    "data": {
                        "state": chat.state.value,
                        "title": chat.title,
                        "updatedAt": chat.updated_at,
                        "sessionUuid": chat.session_uuid or "",
                    },
                })

                turn_completed = True
                break

            elif isinstance(event, ErrorEvent):
                await ws.send_json({"type": "error", "chatId": chat_id, "data": event.message})

    except Exception as e:
        log.exception("Chat %s streaming error: %s", chat_id, e)
        try:
            await ws.send_json({"type": "error", "chatId": chat_id, "data": str(e)})
        except Exception:
            pass

    finally:
        # If the turn didn't complete normally, the chat might be stuck in BUSY.
        # Reap it so it doesn't block future sends.
        if not turn_completed and chat.state == ChatState.BUSY:
            log.warning("Chat %s: streaming ended without turn completion, reaping", chat_id)
            await chat.reap()

    # Signal turn complete
    try:
        await ws.send_json({"type": "done", "chatId": chat_id})
    except Exception:
        pass


# -- Message handlers ---------------------------------------------------------


async def _handle_create_chat(
    ws: WebSocket,
    holster: Holster,
    chats: dict[str, Chat],
) -> None:
    """Handle create-chat: claim from holster, persist, return chatId."""
    try:
        client = await holster.claim()
        chat_id = generate_chat_id()
        chat = Chat.from_holster(id=chat_id, client=client)
        chats[chat_id] = chat

        # Persist to Redis
        await _persist_chat(chat)

        await ws.send_json({
            "type": "chat-created",
            "chatId": chat_id,
            "data": {"state": chat.state.value},
        })
        log.info("Created chat %s", chat_id)

    except Exception as e:
        log.exception("Failed to create chat: %s", e)
        await ws.send_json({"type": "error", "data": f"Failed to create chat: {e}"})


async def _handle_list_chats(
    ws: WebSocket,
    chats: dict[str, Chat],
) -> None:
    """Handle list-chats: return chat metadata from Redis, overlaid with live state."""
    chat_list = await _list_chats_from_redis()

    # Overlay runtime state from live in-memory chats.
    # Redis always reports "dead" — only live chats have real state.
    for item in chat_list:
        live_chat = chats.get(item["chatId"])
        if live_chat:
            item["state"] = live_chat.state.value
            item["title"] = live_chat.title or item["title"]
            item["sessionUuid"] = live_chat.session_uuid or item.get("sessionUuid", "")

    await ws.send_json({"type": "chat-list", "data": chat_list})


async def _handle_send(
    ws: WebSocket,
    chats: dict[str, Chat],
    holster: Holster,
    chat_id: str,
    raw_content: str | list,
) -> None:
    """Handle send: route message to the right chat, stream response."""
    if not chat_id:
        await ws.send_json({"type": "error", "data": "Missing chatId"})
        return

    # Find or load the chat
    chat = chats.get(chat_id)
    if not chat:
        chat = await _load_chat_from_redis(chat_id)
        if chat:
            chats[chat_id] = chat
        else:
            await ws.send_json({"type": "error", "chatId": chat_id, "data": "Chat not found"})
            await ws.send_json({"type": "done", "chatId": chat_id})
            return

    # Normalize content to content blocks
    if isinstance(raw_content, str):
        content: list[dict] = [{"type": "text", "text": raw_content}]
    elif isinstance(raw_content, list):
        content = raw_content
    else:
        content = [{"type": "text", "text": str(raw_content)}]

    try:
        # Resurrect if DEAD
        if chat.state == ChatState.DEAD:
            if not chat.session_uuid:
                await ws.send_json({
                    "type": "error",
                    "chatId": chat_id,
                    "data": "Chat is dead with no session to resume",
                })
                await ws.send_json({"type": "done", "chatId": chat_id})
                return

            await ws.send_json({
                "type": "chat-state",
                "chatId": chat_id,
                "data": {"state": "starting", "sessionUuid": chat.session_uuid or ""},
            })
            await chat.resurrect()
            await ws.send_json({
                "type": "chat-state",
                "chatId": chat_id,
                "data": {"state": chat.state.value, "sessionUuid": chat.session_uuid or ""},
            })

        # Send the message (IDLE -> BUSY, sets title + updated_at)
        await chat.send(content)

        # Notify state change: IDLE -> BUSY
        await ws.send_json({
            "type": "chat-state",
            "chatId": chat_id,
            "data": {
                "state": chat.state.value,
                "title": chat.title,
                "updatedAt": chat.updated_at,
                "sessionUuid": chat.session_uuid or "",
            },
        })

        # Persist updated metadata
        asyncio.create_task(_persist_chat(chat))

        # Stream events (ends with chat-state IDLE + done)
        await _stream_chat_events(ws, chat)

    except Exception as e:
        log.exception("Chat %s send error: %s", chat_id, e)
        await ws.send_json({"type": "error", "chatId": chat_id, "data": str(e)})
        await ws.send_json({"type": "done", "chatId": chat_id})


async def _handle_interrupt(
    ws: WebSocket,
    chats: dict[str, Chat],
    chat_id: str,
) -> None:
    """Handle interrupt: cancel the active turn for a specific chat."""
    if not chat_id:
        await ws.send_json({"type": "error", "data": "Missing chatId"})
        return

    chat = chats.get(chat_id)
    if chat:
        try:
            await chat.interrupt()
            await ws.send_json({
                "type": "chat-state",
                "chatId": chat_id,
                "data": {"state": chat.state.value, "sessionUuid": chat.session_uuid or ""},
            })
        except Exception as e:
            log.exception("Interrupt error: %s", e)
            await ws.send_json({"type": "error", "chatId": chat_id, "data": str(e)})

    await ws.send_json({"type": "interrupted", "chatId": chat_id})


# -- WebSocket handler --------------------------------------------------------


@router.websocket("/ws")
async def websocket_chat(ws: WebSocket) -> None:
    """Bidirectional multi-chat over WebSocket.

    Phase 2: All messages carry chatId. Multiple chats multiplexed
    over one connection. The create-chat round-trip is instant thanks
    to the Holster.
    """
    await ws.accept()
    log.info("WebSocket connected")

    holster: Holster = ws.app.state.holster
    chats: dict[str, Chat] = ws.app.state.chats

    try:
        while True:
            raw = await ws.receive_json()
            msg_type = raw.get("type")

            if msg_type == "create-chat":
                await _handle_create_chat(ws, holster, chats)

            elif msg_type == "list-chats":
                await _handle_list_chats(ws, chats)

            elif msg_type == "send":
                chat_id = raw.get("chatId", "")
                raw_content = raw.get("content", "")
                log.info(
                    "Send: chat=%s",
                    chat_id[:8] if chat_id else "?",
                )
                await _handle_send(ws, chats, holster, chat_id, raw_content)

            elif msg_type == "interrupt":
                chat_id = raw.get("chatId", "")
                log.info(
                    "Interrupt: chat=%s",
                    chat_id[:8] if chat_id else "?",
                )
                await _handle_interrupt(ws, chats, chat_id)

            else:
                log.warning("Unknown message type: %s", msg_type)

    except WebSocketDisconnect:
        log.info("WebSocket disconnected")
    except Exception as e:
        log.exception("WebSocket error: %s", e)
    finally:
        log.info("WebSocket closed")

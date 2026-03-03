"""WebSocket route — bidirectional conversation pipe.

Phase 1: Single-chat mode with Chat + Holster internals.
Same WebSocket protocol as before (no chatId yet). Same behavior. Different guts.

Client -> Server messages:
  { "type": "send", "content": "Hello", "sessionId": "..." }
  { "type": "send", "content": [{ "type": "text", "text": "Hello" }, ...] }
  { "type": "interrupt" }

Server -> Client messages:
  { "type": "text-delta", "data": "chunk" }
  { "type": "thinking-delta", "data": "chunk" }
  { "type": "tool-call", "data": { "toolCallId", "toolName", "args", "argsText" } }
  { "type": "session-id", "data": "abc-123..." }
  { "type": "error", "data": "something broke" }
  { "type": "done" }
  { "type": "interrupted" }
"""

import asyncio
import json
import logging
import os
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from alpha_sdk import AssistantEvent, ResultEvent, ErrorEvent, StreamEvent

from alpha_app.chat import Chat, ChatState, Holster, generate_chat_id

log = logging.getLogger(__name__)

router = APIRouter()


# -- Session logic --------------------------------------------------------


async def _ensure_chat(
    current: Chat | None,
    holster: Holster,
    session_id: str | None,
) -> Chat:
    """Ensure we have a Chat connected to the right session.

    Phase 1 session logic (matches MannekinClient behavior):
    - session_id=None, no chat    -> new session (claim from holster)
    - session_id matches chat     -> reuse (resurrect if DEAD)
    - session_id=None, chat fresh -> reuse (first turn, no UUID yet)
    - session_id mismatch         -> reap old, create new
    """
    # No active chat — create one
    if current is None:
        if session_id:
            # Resuming a previous session (page reload, etc.)
            chat = Chat(id=generate_chat_id())
            await chat.resurrect(session_id)
            return chat
        else:
            # Brand new conversation
            client = await holster.claim()
            return Chat.from_holster(id=generate_chat_id(), client=client)

    # Active chat matches the requested session — reuse
    if session_id is not None and session_id == current.session_uuid:
        if current.state == ChatState.DEAD:
            await current.resurrect()
        return current

    # First turn of an existing chat (no UUID yet, session_id=None)
    if session_id is None and current.session_uuid is None and current.state != ChatState.DEAD:
        return current

    # Session mismatch — reap old, create new
    log.info(
        "Session change: %s -> %s",
        current.session_uuid[:8] if current.session_uuid else "None",
        session_id[:8] if session_id else "None",
    )
    if current.state != ChatState.DEAD:
        await current.reap()

    if session_id:
        chat = Chat(id=generate_chat_id())
        await chat.resurrect(session_id)
        return chat
    else:
        client = await holster.claim()
        return Chat.from_holster(id=generate_chat_id(), client=client)


# -- Redis persistence ----------------------------------------------------


async def _upsert_session_redis(session_id: str, title: str) -> None:
    """Upsert session metadata into Redis."""
    try:
        import redis.asyncio as aioredis

        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        r = aioredis.from_url(redis_url, decode_responses=True)
        try:
            now = str(time.time())
            pipe = r.pipeline()
            pipe.zadd("alpha:sessions", {session_id: float(now)})
            pipe.hset(f"alpha:session:{session_id}", mapping={
                "title": title[:80],
                "updated_at": now,
            })
            pipe.hsetnx(f"alpha:session:{session_id}", "created_at", now)
            await pipe.execute()
        finally:
            await r.aclose()
    except Exception as e:
        log.warning("Redis upsert failed (non-fatal): %s", e)


# -- Event streaming ------------------------------------------------------


async def _stream_chat_events(ws: WebSocket, chat: Chat, user_text: str) -> None:
    """Read events from the Chat and push them to the browser.

    Runs until the Chat emits a ResultEvent (turn complete) or errors out.
    If streaming ends abnormally, the chat gets reaped to prevent stuck BUSY state.
    """
    turn_completed = False

    try:
        async for event in chat.events():
            if isinstance(event, StreamEvent):
                if event.delta_type == "text_delta":
                    text = event.delta_text
                    if text:
                        await ws.send_json({"type": "text-delta", "data": text})
                elif event.delta_type == "thinking_delta":
                    text = event.delta_text
                    if text:
                        await ws.send_json({"type": "thinking-delta", "data": text})

            elif isinstance(event, AssistantEvent):
                for block in event.content:
                    if block.get("type") == "tool_use":
                        await ws.send_json({
                            "type": "tool-call",
                            "data": {
                                "toolCallId": block.get("id", ""),
                                "toolName": block.get("name", ""),
                                "args": block.get("input", {}),
                                "argsText": json.dumps(block.get("input", {})),
                            },
                        })

            elif isinstance(event, ResultEvent):
                sid = chat.session_uuid
                await ws.send_json({"type": "session-id", "data": sid})

                if sid:
                    asyncio.create_task(_upsert_session_redis(sid, user_text))

                turn_completed = True
                break

            elif isinstance(event, ErrorEvent):
                await ws.send_json({"type": "error", "data": event.message})

    except Exception as e:
        log.exception("Chat streaming error: %s", e)
        try:
            await ws.send_json({"type": "error", "data": str(e)})
        except Exception:
            pass

    finally:
        # If the turn didn't complete normally, the chat might be stuck in BUSY.
        # Reap it so it doesn't block future sends.
        if not turn_completed and chat.state == ChatState.BUSY:
            log.warning("Chat %s: streaming ended without turn completion, reaping", chat.id)
            await chat.reap()

    # Signal turn complete
    try:
        await ws.send_json({"type": "done"})
    except Exception:
        pass


# -- WebSocket handler ----------------------------------------------------


@router.websocket("/ws")
async def websocket_chat(ws: WebSocket) -> None:
    """Bidirectional chat over WebSocket.

    Phase 1: Single-chat mode. Same protocol as before.
    The connection stays open for the lifetime of the tab.
    Client sends messages, server pushes events.
    """
    await ws.accept()
    log.info("WebSocket connected")

    holster: Holster = ws.app.state.holster

    try:
        while True:
            # Wait for a message from the browser
            raw = await ws.receive_json()
            msg_type = raw.get("type")

            if msg_type == "send":
                raw_content = raw.get("content", "")
                session_id = raw.get("sessionId")

                # Normalize to content blocks
                if isinstance(raw_content, str):
                    content: list[dict] = [{"type": "text", "text": raw_content}]
                elif isinstance(raw_content, list):
                    content = raw_content
                else:
                    content = [{"type": "text", "text": str(raw_content)}]

                # Extract text for session title
                user_text = " ".join(
                    block.get("text", "")
                    for block in content
                    if block.get("type") == "text"
                )

                log.info(
                    "Send: session=%s blocks=%d",
                    session_id[:8] if session_id else "new",
                    len(content),
                )

                try:
                    ws.app.state.active_chat = await _ensure_chat(
                        ws.app.state.active_chat, holster, session_id,
                    )
                    chat = ws.app.state.active_chat
                    await chat.send(content)
                    await _stream_chat_events(ws, chat, user_text)
                except Exception as e:
                    log.exception("Chat error: %s", e)
                    await ws.send_json({"type": "error", "data": str(e)})
                    await ws.send_json({"type": "done"})

            elif msg_type == "interrupt":
                log.info("Interrupt requested")
                chat = ws.app.state.active_chat
                if chat:
                    try:
                        await chat.interrupt()
                        await ws.send_json({"type": "interrupted"})
                    except Exception as e:
                        log.exception("Interrupt error: %s", e)
                        await ws.send_json({"type": "error", "data": str(e)})
                else:
                    await ws.send_json({"type": "interrupted"})

            else:
                log.warning("Unknown message type: %s", msg_type)

    except WebSocketDisconnect:
        log.info("WebSocket disconnected")
    except Exception as e:
        log.exception("WebSocket error: %s", e)
    finally:
        log.info("WebSocket closed")

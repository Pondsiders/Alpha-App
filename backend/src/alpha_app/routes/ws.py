"""WebSocket route — bidirectional conversation pipe.

Replaces the POST+SSE pattern with a persistent connection.
Either side can send at any time. No request-response. Just an open line.

Client → Server messages:
  { "type": "send", "content": "Hello" }
  { "type": "send", "content": [{ "type": "text", "text": "Hello" }, ...] }
  { "type": "interrupt" }

Server → Client messages:
  { "type": "text-delta", "data": "chunk" }
  { "type": "thinking-delta", "data": "chunk" }
  { "type": "tool-call", "data": { "toolCallId", "toolName", "args", "argsText" } }
  { "type": "session-id", "data": "abc-123..." }
  { "type": "error", "data": "something broke" }
  { "type": "done" }
"""

import asyncio
import json
import logging
import os
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from alpha_sdk import AssistantEvent, ResultEvent, ErrorEvent, StreamEvent

from alpha_app.client import client

log = logging.getLogger(__name__)

router = APIRouter()


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


async def _stream_kernel_events(ws: WebSocket, user_text: str) -> None:
    """Read events from the kernel and push them to the browser.

    Runs until the kernel emits a ResultEvent (turn complete).
    """
    try:
        async for event in client.events():
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
                sid = event.session_id
                client.update_session_id(sid)
                log.info("Result: session=%s cost=$%.4f", sid[:8] if sid else "?", event.cost_usd)
                await ws.send_json({"type": "session-id", "data": sid})

                if sid:
                    asyncio.create_task(_upsert_session_redis(sid, user_text))

                # Turn complete — break out of event loop
                break

            elif isinstance(event, ErrorEvent):
                await ws.send_json({"type": "error", "data": event.message})

    except Exception as e:
        log.exception("Kernel streaming error: %s", e)
        try:
            await ws.send_json({"type": "error", "data": str(e)})
        except Exception:
            pass

    # Signal turn complete
    try:
        await ws.send_json({"type": "done"})
    except Exception:
        pass


@router.websocket("/ws")
async def websocket_chat(ws: WebSocket) -> None:
    """Bidirectional chat over WebSocket.

    The connection stays open for the lifetime of the tab.
    Client sends messages, server pushes events.
    """
    await ws.accept()
    log.info("WebSocket connected")

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
                    await client.ensure_session(session_id)
                    await client.send(content)
                    # Stream kernel events to the browser
                    await _stream_kernel_events(ws, user_text)
                except Exception as e:
                    log.exception("Chat error: %s", e)
                    await ws.send_json({"type": "error", "data": str(e)})
                    await ws.send_json({"type": "done"})

            elif msg_type == "interrupt":
                log.info("Interrupt requested")
                try:
                    await client.shutdown()
                    await ws.send_json({"type": "interrupted"})
                except Exception as e:
                    log.exception("Interrupt error: %s", e)
                    await ws.send_json({"type": "error", "data": str(e)})

            else:
                log.warning("Unknown message type: %s", msg_type)

    except WebSocketDisconnect:
        log.info("WebSocket disconnected")
    except Exception as e:
        log.exception("WebSocket error: %s", e)
    finally:
        log.info("WebSocket closed")

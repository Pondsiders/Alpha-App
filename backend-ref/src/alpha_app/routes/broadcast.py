"""broadcast.py — Send events to all connected WebSockets.

The heart of the switch. Every event that should reach all clients
flows through here. Dead connections are silently pruned.
"""

import asyncio
import os

import logfire
from fastapi import WebSocket


def _trace_broadcast_enabled() -> bool:
    """Lazy check — read at call time so load_dotenv has run."""
    return os.environ.get("ALPHA_TRACE_WS_BROADCAST", "").strip() == "1"


async def broadcast(
    connections: set,
    event: dict,
    *,
    exclude: WebSocket | None = None,
) -> None:
    """Send event to all connected WebSockets, optionally excluding one.

    Dead connections (send fails) are silently removed from the set.
    Uses asyncio.gather for parallel delivery.
    """
    targets = [c for c in connections if c is not exclude]
    if not targets:
        return

    # Trace outbound WebSocket events — gated behind ALPHA_TRACE_WS_BROADCAST.
    if _trace_broadcast_enabled():
        event_type = event.get("type", "?")
        chat_id = event.get("chatId", "")
        preview = ""
        if event_type in ("text-delta", "thinking-delta"):
            data = event.get("data", "")
            preview = repr(data[:40]) if data else ""
        elif event_type in ("tool-use-start", "tool-call"):
            data = event.get("data", {})
            if isinstance(data, dict):
                preview = data.get("toolName", "")
        elif event_type == "tool-result":
            data = event.get("data", {})
            if isinstance(data, dict):
                preview = data.get("toolCallId", "")[:12]
        elif event_type == "chat-state":
            data = event.get("data", {})
            if isinstance(data, dict):
                preview = data.get("state", "")
        elif event_type == "assistant-message":
            data = event.get("data", {})
            if isinstance(data, dict):
                parts = data.get("parts", [])
                for p in parts:
                    if p.get("type") == "text":
                        text = p.get("text", "")
                        preview = (text[:50] + "…") if len(text) > 50 else text
                        break
        logfire.trace(
            "ws.broadcast: {event_type} {preview}",
            event_type=event_type,
            preview=preview,
            chat_id=chat_id,
        )

    results = await asyncio.gather(
        *(c.send_json(event) for c in targets),
        return_exceptions=True,
    )
    for conn, result in zip(targets, results):
        if isinstance(result, Exception):
            connections.discard(conn)

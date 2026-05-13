"""Registry of live WebSockets. Module-level state guarded by an asyncio lock.

`register` and `unregister` are called by the WebSocket router around the
dispatch loop. `broadcast` serializes a `BaseEvent` and ships it to every
currently-registered socket; a socket whose send fails is removed from the
set.

Module-level state because there is one set of connections per process.
A class wrapper would be ceremony.
"""

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

from alpha.ws.events import BaseEvent

_logger = logging.getLogger(__name__)

_connections: set[WebSocket] = set()
_lock = asyncio.Lock()


async def register(websocket: WebSocket) -> None:
    """Add a WebSocket to the broadcast set."""
    async with _lock:
        _connections.add(websocket)


async def unregister(websocket: WebSocket) -> None:
    """Remove a WebSocket from the broadcast set. Idempotent."""
    async with _lock:
        _connections.discard(websocket)


async def broadcast(event: BaseEvent) -> None:
    """Send `event` to every registered WebSocket, concurrently.

    Sends are issued in parallel; a slow socket doesn't hold up the
    others. Sockets whose send raises are unregistered.
    """
    payload: dict[str, Any] = event.model_dump(
        by_alias=True, exclude_none=True, mode="json"
    )
    async with _lock:
        recipients = list(_connections)
    _ = await asyncio.gather(*(_send_one(ws, payload) for ws in recipients))


async def _send_one(websocket: WebSocket, payload: dict[str, Any]) -> None:
    """Send `payload` to one socket; unregister it on failure."""
    try:
        await websocket.send_json(payload)
    except Exception:
        _logger.warning("broadcast send failed; unregistering socket", exc_info=True)
        await unregister(websocket)

"""broadcast.py — Send events to all connected WebSockets.

The heart of the switch. Every event that should reach all clients
flows through here. Dead connections are silently pruned.
"""

import asyncio

from fastapi import WebSocket

# Per-chat monotonic sequence counters.
# The asyncio event loop is single-threaded, so incrementing here (before any
# await) is race-free. seq captures true broadcast order regardless of which
# Postgres pool connection commits first.
_seq_counters: dict[str, int] = {}


async def broadcast(
    connections: set,
    event: dict,
    *,
    exclude: WebSocket | None = None,
) -> None:
    """Send event to all connected WebSockets, optionally excluding one.

    Dead connections (send fails) are silently removed from the set.
    Uses asyncio.gather for parallel delivery.

    Events carrying a chatId are persisted to the Postgres event store
    (fire-and-forget via asyncio.create_task — never blocks the hot path).
    Each stored event carries a monotonically increasing seq number so
    replay_events() can ORDER BY seq for correct ordering.
    """
    # Persist to event store — fire-and-forget, never blocks streaming
    if "chatId" in event:
        from alpha_app.db import store_event
        chat_id = event["chatId"]
        seq = _seq_counters.get(chat_id, 0)
        _seq_counters[chat_id] = seq + 1
        asyncio.create_task(store_event(chat_id, event, seq))

    targets = [c for c in connections if c is not exclude]
    if not targets:
        return
    results = await asyncio.gather(
        *(c.send_json(event) for c in targets),
        return_exceptions=True,
    )
    for conn, result in zip(targets, results):
        if isinstance(result, Exception):
            connections.discard(conn)

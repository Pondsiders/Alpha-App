"""Unit tests for `alpha.ws.connections`.

Two contracts:
- broadcast reaches every registered socket
- a socket whose send raises is unregistered

A fake WebSocket records `send_json` calls and can be set to raise on
send. The registry uses module-level state; each test clears it via the
autouse fixture.
"""

# Fakes mirror FastAPI WebSocket; many params/methods are unused or stubbed.
# Tests legitimately reach into module-level state for fixture cleanup.
# pyright: reportUnusedParameter=false, reportPrivateUsage=false, reportUnusedFunction=false

from typing import Any

import pytest

from alpha.ws import connections
from alpha.ws.events import BaseEvent


class _FakeWebSocket:
    """Records sent payloads. Optionally raises on send."""

    def __init__(self, *, raise_on_send: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self.raise_on_send: bool = raise_on_send

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self.raise_on_send:
            raise RuntimeError("socket dead")
        self.sent.append(payload)


class _PingEvent(BaseEvent):
    """Minimal concrete BaseEvent for tests."""

    event: str = "ping"


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    """Reset module-level state before each test."""
    connections._connections.clear()


async def test_broadcast_sends_to_every_registered_socket() -> None:
    ws_a = _FakeWebSocket()
    ws_b = _FakeWebSocket()
    await connections.register(ws_a)  # pyright: ignore[reportArgumentType]
    await connections.register(ws_b)  # pyright: ignore[reportArgumentType]

    await connections.broadcast(_PingEvent())

    assert ws_a.sent == [{"event": "ping"}]
    assert ws_b.sent == [{"event": "ping"}]


async def test_broadcast_unregisters_failed_socket() -> None:
    """A socket whose send_json raises is removed from the registry."""
    good = _FakeWebSocket()
    dead = _FakeWebSocket(raise_on_send=True)
    await connections.register(good)  # pyright: ignore[reportArgumentType]
    await connections.register(dead)  # pyright: ignore[reportArgumentType]

    await connections.broadcast(_PingEvent())

    assert good.sent == [{"event": "ping"}]
    assert dead not in connections._connections
    assert good in connections._connections

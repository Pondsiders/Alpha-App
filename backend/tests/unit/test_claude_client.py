"""Unit tests for `alpha.claude_client.ClaudeClient`.

These tests exercise only OUR code — the idempotency guards on
`connect()` / `disconnect()` and the "not connected" guards on
`send()` / `events()` / `interrupt()`. The actual subprocess
spawning, streaming, and SDK behavior is tested by the e2e suite
against MockAnthropic.

No subprocess is spawned by these tests; `ClaudeSDKClient` is
monkeypatched to a recording stub.
"""

# Stub mirrors SDK signatures; some params are unused, the fixture is
# pytest-discovered. Both are fine in test infrastructure.
# pyright: reportUnusedParameter=false, reportUnusedFunction=false

from typing import Any, ClassVar, final

import pytest

from alpha import claude_client
from alpha.claude_client import ClaudeClient


@final
class _StubSDKClient:
    """Records connect/disconnect/interrupt calls; spawns nothing."""

    instances: ClassVar[list["_StubSDKClient"]] = []

    def __init__(self, options: Any) -> None:
        self.options = options
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.interrupt_calls = 0
        _StubSDKClient.instances.append(self)

    async def connect(self, prompt: Any = None) -> None:
        self.connect_calls += 1

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def interrupt(self) -> None:
        self.interrupt_calls += 1

    def receive_messages(self) -> Any:  # pragma: no cover — not exercised
        return iter([])


@pytest.fixture(autouse=True)
def _stub_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace `ClaudeSDKClient` with a recording stub for every test."""
    _StubSDKClient.instances = []
    monkeypatch.setattr(claude_client, "ClaudeSDKClient", _StubSDKClient)


async def test_send_before_connect_raises() -> None:
    client = ClaudeClient()
    with pytest.raises(RuntimeError, match="send"):
        await client.send([{"type": "text", "text": "hi"}])


async def test_events_before_connect_raises() -> None:
    client = ClaudeClient()
    with pytest.raises(RuntimeError, match="events"):
        _ = client.events()


async def test_interrupt_before_connect_raises() -> None:
    client = ClaudeClient()
    with pytest.raises(RuntimeError, match="interrupt"):
        await client.interrupt()


async def test_disconnect_before_connect_is_noop() -> None:
    client = ClaudeClient()
    await client.disconnect()
    assert client.connected is False
    assert _StubSDKClient.instances == []  # no SDK ever constructed


async def test_double_connect_is_noop() -> None:
    """The SDK silently leaks subprocesses on double-connect; we guard."""
    client = ClaudeClient()
    await client.connect()
    await client.connect()
    assert len(_StubSDKClient.instances) == 1
    assert _StubSDKClient.instances[0].connect_calls == 1
    await client.disconnect()


async def test_double_disconnect_is_noop() -> None:
    client = ClaudeClient()
    await client.connect()
    await client.disconnect()
    await client.disconnect()
    assert _StubSDKClient.instances[0].disconnect_calls == 1

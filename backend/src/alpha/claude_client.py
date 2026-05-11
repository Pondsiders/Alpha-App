"""ClaudeClient — one wake/reap cycle of `claude_agent_sdk.ClaudeSDKClient`.

Wraps the SDK's streaming-input mode. One client object per cycle:
`connect()` spawns the subprocess, `send()` queues user turns onto the
input stream, `events()` drains responses, `disconnect()` reaps. Both
connect and disconnect are idempotent.

The session_id captured from a prior cycle's events is held by Chat; on
revival, Chat constructs a fresh ClaudeClient with that session_id and
the SDK rehydrates the JSONL transcript via `resume`. The soul prompt
is read from disk on each `connect()`, so editing it takes effect on
the next wake without a process restart.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Final

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
)

from alpha.settings import settings

# Sentinel pushed onto the input queue to end the streaming generator.
# Identity comparison only; never inspected.
_END_OF_INPUT = object()


class ClaudeClient:
    """Wrap one wake/reap cycle of `ClaudeSDKClient` in streaming-input mode."""

    def __init__(
        self,
        *,
        session_id: str | None = None,
        fork_session: bool = False,
        env_override: dict[str, str] | None = None,
    ) -> None:
        """Build the client. No subprocess yet; `connect()` spawns it."""
        self._session_id: Final[str | None] = session_id
        self._fork_session: Final[bool] = fork_session
        self._env_override: Final[dict[str, str]] = env_override or {}
        self._sdk: ClaudeSDKClient | None = None
        self._input_queue: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue()
        self._connected: bool = False

    async def connect(self) -> None:
        """Spawn the subprocess and start the streaming-input query."""
        if self._connected:
            return
        options = ClaudeAgentOptions(
            system_prompt=settings.soul_doc.read_text(),
            cwd=str(settings.working_directory),
            plugins=[{"type": "local", "path": str(settings.je_ne_sais_quoi)}],
            model="claude-opus-4-7[1m]",
            permission_mode="bypassPermissions",
            setting_sources=["project", "local"],
            thinking={"type": "adaptive"},
            effort="xhigh",
            include_partial_messages=True,
            include_hook_events=True,
            resume=self._session_id,
            fork_session=self._fork_session,
            env=self._env_override,
        )
        self._sdk = ClaudeSDKClient(options=options)
        await self._sdk.connect(self._input_generator())
        self._connected = True

    async def disconnect(self) -> None:
        """End the input stream and reap the subprocess."""
        if not self._connected:
            return
        await self._input_queue.put(_END_OF_INPUT)
        assert self._sdk is not None  # noqa: S101 — invariant when _connected
        await self._sdk.disconnect()
        self._sdk = None
        self._connected = False

    @property
    def connected(self) -> bool:
        """True between `connect()` and `disconnect()`."""
        return self._connected

    async def send(self, content: list[dict[str, Any]]) -> None:
        """Queue one user turn for delivery."""
        if not self._connected:
            msg = "ClaudeClient: send() before connect()"
            raise RuntimeError(msg)
        await self._input_queue.put(
            {
                "type": "user",
                "message": {"role": "user", "content": content},
            }
        )

    def events(self) -> AsyncIterator[Any]:
        """Iterate SDK message objects until the subprocess ends."""
        if not self._connected:
            msg = "ClaudeClient: events() before connect()"
            raise RuntimeError(msg)
        assert self._sdk is not None  # noqa: S101 — invariant when _connected
        return self._sdk.receive_messages()

    async def interrupt(self) -> None:
        """Cancel the current turn in flight."""
        if not self._connected:
            msg = "ClaudeClient: interrupt() before connect()"
            raise RuntimeError(msg)
        assert self._sdk is not None  # noqa: S101 — invariant when _connected
        await self._sdk.interrupt()

    async def _input_generator(self) -> AsyncIterator[dict[str, Any]]:
        """Drain the input queue forever; the sentinel ends the stream."""
        while True:
            item = await self._input_queue.get()
            if item is _END_OF_INPUT:
                return
            assert isinstance(item, dict)  # noqa: S101 — narrows past the sentinel
            yield item

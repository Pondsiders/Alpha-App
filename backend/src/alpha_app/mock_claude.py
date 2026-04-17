"""mock_claude.py — Drop-in replacement for Claude in tests.

Same interface as Claude (start, send, stop, on_event callback) but no
subprocess, no proxy, no network. Records what it receives via send()
so tests can assert on the content blocks that arrived.

Usage:
    claude = MockClaude(system_prompt="You are a frog.")
    claude.on_event = my_handler     # events flow through callback
    await claude.start()
    await claude.send([{"type": "text", "text": "Hello!"}])

    # Inspect what was sent:
    claude.sent_messages  # list of content block lists
"""

from __future__ import annotations

import uuid

from alpha_app.constants import CONTEXT_WINDOW
from dataclasses import dataclass, field

from .claude import (
    AssistantEvent,
    ClaudeState,
    Event,
    InitEvent,
    ResultEvent,
    SystemEvent,
)


# Default canned response text
_CANNED_RESPONSE = "Rubber baby buggy bumpers right back at you!"


class MockClaude:
    """A mock Claude that records send() calls and yields canned responses.

    Implements the same public interface as Claude:
        start(), send(), events(), stop()
        Properties: state, session_id, pid, token_count, context_window, etc.
    """

    def __init__(
        self,
        model: str = "mock-model",
        system_prompt: str | None = None,
        mcp_config: str | None = None,
        permission_mode: str = "bypassPermissions",
        extra_args: list[str] | None = None,
        mcp_servers: dict | None = None,
        permission_handler=None,
        canned_response: str = _CANNED_RESPONSE,
    ):
        self.model = model
        self.system_prompt = system_prompt
        self.mcp_config = mcp_config
        self.permission_mode = permission_mode
        self.extra_args = extra_args or []
        self._mcp_servers = mcp_servers or {}
        self._permission_handler = permission_handler
        self._canned_response = canned_response

        self._state = ClaudeState.IDLE
        self._session_id: str | None = None

        # The assertion surface: every send() appends here
        self.sent_messages: list[list[dict]] = []

    @property
    def state(self) -> ClaudeState:
        return self._state

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def pid(self) -> int | None:
        return None

    # -- Token state (all stubbed) -------------------------------------------

    @property
    def token_count(self) -> int:
        return 10000  # Arbitrary, non-zero so context meter has something

    @property
    def context_window(self) -> int:
        return CONTEXT_WINDOW

    @property
    def input_tokens(self) -> int:
        return 5000

    @property
    def total_input_tokens(self) -> int:
        return 5000

    @property
    def cache_creation_tokens(self) -> int:
        return 0

    @property
    def cache_read_tokens(self) -> int:
        return 0

    @property
    def output_tokens(self) -> int:
        return 500

    @property
    def stop_reason(self) -> str | None:
        return "end_turn"

    @property
    def response_model(self) -> str | None:
        return self.model

    @property
    def response_id(self) -> str | None:
        return "mock-response-id"

    def reset_token_count(self) -> None:
        pass

    def reset_output_tokens(self) -> None:
        pass

    # -- Lifecycle ------------------------------------------------------------

    async def start(self, session_id: str | None = None) -> None:
        if self._state != ClaudeState.IDLE:
            raise RuntimeError(f"Cannot start in state {self._state}")

        self._state = ClaudeState.READY
        self._session_id = session_id or str(uuid.uuid4())

    async def send(self, content: list[dict]) -> None:
        if self._state != ClaudeState.READY:
            raise RuntimeError(f"Cannot send in state {self._state}")

        # Record the content blocks — this is what tests assert on
        self.sent_messages.append(content)

    async def stop(self) -> None:
        self._state = ClaudeState.STOPPED

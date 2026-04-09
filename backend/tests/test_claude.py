"""test_claude.py — Tests for the Claude class.

Tests the Event dataclass contracts and state machine guards.
Wire protocol parsing is now handled by the Agent SDK internally.

What's NOT here (and why):
- _parse_event tests: parsing is now in ClaudeSDKClient, not our code.
- Config storage tests: trust Python.
- Proxy tests: proxy was removed (SDK handles token counting natively).
"""

import pytest

from alpha_app.claude import (
    Claude,
    ClaudeState,
    Event,
    InitEvent,
    AssistantEvent,
    ResultEvent,
    StreamEvent,
    SystemEvent,
    ErrorEvent,
)


# -- Event dataclass contracts ------------------------------------------------


class TestEventDataclasses:
    """Event types have the right shape and defaults."""

    def test_assistant_text_concatenation(self):
        """Multiple text blocks → single .text string."""
        event = AssistantEvent(
            raw={},
            content=[
                {"type": "text", "text": "Hello "},
                {"type": "text", "text": "world!"},
            ],
        )
        assert event.text == "Hello world!"

    def test_assistant_tool_use_excluded_from_text(self):
        """Tool use blocks in content but excluded from .text."""
        event = AssistantEvent(
            raw={},
            content=[
                {"type": "text", "text": "Let me check. "},
                {"type": "tool_use", "id": "tool_01", "name": "Bash"},
                {"type": "text", "text": "Done."},
            ],
        )
        assert event.text == "Let me check. Done."
        assert len(event.content) == 3

    def test_stream_event_text_delta(self):
        event = StreamEvent(raw={}, inner={
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "Hello"},
        })
        assert event.delta_text == "Hello"
        assert event.index == 1

    def test_stream_event_thinking_delta(self):
        event = StreamEvent(raw={}, inner={
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "Let me think..."},
        })
        assert event.delta_text == "Let me think..."

    def test_stream_event_input_json_delta(self):
        event = StreamEvent(raw={}, inner={
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": '{"mem'},
        })
        assert event.delta_type == "input_json_delta"
        assert event.delta_partial_json == '{"mem'
        assert event.delta_text == ""  # Not text

    def test_stream_event_block_start(self):
        event = StreamEvent(raw={}, inner={
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_01ABC",
                "name": "Bash",
            },
        })
        assert event.event_type == "content_block_start"
        assert event.block_type == "tool_use"
        assert event.block_id == "toolu_01ABC"
        assert event.block_name == "Bash"

    def test_result_event_defaults(self):
        event = ResultEvent(raw={})
        assert event.session_id == ""
        assert event.cost_usd == 0.0
        assert event.is_error is False

    def test_system_event_subtype(self):
        event = SystemEvent(raw={}, subtype="init")
        assert event.subtype == "init"


# -- State machine -----------------------------------------------------------


class TestClaudeState:
    """Claude state transitions and guards."""

    def test_initial_state_is_idle(self):
        claude = Claude()
        assert claude.state == ClaudeState.IDLE

    @pytest.mark.asyncio
    async def test_cannot_send_in_idle(self):
        """Sending before start raises RuntimeError."""
        claude = Claude()
        with pytest.raises(RuntimeError, match="Cannot send"):
            await claude.send([{"type": "text", "text": "hello"}])

    @pytest.mark.asyncio
    async def test_cannot_start_twice(self):
        """Starting when not IDLE raises RuntimeError."""
        claude = Claude()
        claude._state = ClaudeState.RUNNING
        with pytest.raises(RuntimeError, match="Cannot start"):
            await claude.start()


# -- Token accounting --------------------------------------------------------


class TestTokenAccounting:
    """Token count properties and reset methods."""

    def test_total_input_tokens(self):
        claude = Claude()
        claude._input_tokens = 100
        claude._cache_creation_tokens = 50
        claude._cache_read_tokens = 200
        assert claude.total_input_tokens == 350

    def test_reset_token_count(self):
        claude = Claude()
        claude._input_tokens = 100
        claude._cache_creation_tokens = 50
        claude._cache_read_tokens = 200
        claude.reset_token_count()
        assert claude.total_input_tokens == 0

    def test_reset_output_tokens(self):
        claude = Claude()
        claude._output_tokens = 500
        claude.reset_output_tokens()
        assert claude.output_tokens == 0

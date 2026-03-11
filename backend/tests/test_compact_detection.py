"""test_compact_detection.py — Tests for compact_boundary detection in streaming.

Verifies that stream_chat_events() detects SystemEvent with
subtype "compact_boundary" and sets chat._needs_orientation = True.
"""

from unittest.mock import AsyncMock, patch

import pytest

from alpha_app.claude import (
    AssistantEvent,
    ResultEvent,
    StreamEvent,
    SystemEvent,
)
from alpha_app.chat import Chat, ConversationState


# -- Helpers ------------------------------------------------------------------


def _make_chat(events_sequence: list) -> Chat:
    """Build a mock Chat that yields a controlled sequence from events().

    The Chat has all attributes streaming.py accesses, with safe defaults.
    _needs_orientation starts False so we can verify it gets set to True.
    """
    chat = AsyncMock(spec=Chat)
    chat.id = "test-chat-id"
    chat.token_count = 1000
    chat.context_window = 200_000
    chat.state = ConversationState.RESPONDING
    chat.title = "Test Chat"
    chat.updated_at = 1700000000.0
    chat.session_uuid = "sess_test123"
    chat._needs_orientation = False

    # check_approach_threshold returns None (no threshold crossed)
    chat.check_approach_threshold = lambda: None

    # events() returns an async iterator over the provided sequence
    async def _events():
        for event in events_sequence:
            yield event

    chat.events = _events

    return chat


def _result_event() -> ResultEvent:
    """Create a standard ResultEvent to end a turn."""
    return ResultEvent(
        raw={"type": "result", "session_id": "sess_test123"},
        session_id="sess_test123",
        cost_usd=0.001,
        num_turns=1,
        duration_ms=500,
        is_error=False,
    )


def _compact_boundary_event() -> SystemEvent:
    """Create a compact_boundary SystemEvent."""
    return SystemEvent(
        raw={
            "type": "system",
            "subtype": "compact_boundary",
            "compact_metadata": {"trigger": "auto", "pre_tokens": 150000},
        },
        subtype="compact_boundary",
    )


def _system_event(subtype: str) -> SystemEvent:
    """Create a SystemEvent with an arbitrary subtype."""
    return SystemEvent(
        raw={"type": "system", "subtype": subtype},
        subtype=subtype,
    )


def _stream_event(text: str) -> StreamEvent:
    """Create a text delta StreamEvent."""
    return StreamEvent(
        raw={"type": "stream_event"},
        inner={
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": text},
        },
    )


def _assistant_event(text: str) -> AssistantEvent:
    """Create an AssistantEvent with text content."""
    return AssistantEvent(
        raw={"type": "assistant"},
        content=[{"type": "text", "text": text}],
    )


# -- Tests --------------------------------------------------------------------


@patch("alpha_app.routes.streaming.persist_chat", new_callable=AsyncMock)
@patch("alpha_app.routes.streaming.broadcast", new_callable=AsyncMock)
class TestCompactBoundaryDetection:
    """Verify compact_boundary SystemEvent sets _needs_orientation."""

    async def test_compact_boundary_sets_flag(self, mock_broadcast, mock_persist):
        """A SystemEvent with subtype 'compact_boundary' sets _needs_orientation = True."""
        from alpha_app.routes.streaming import stream_chat_events

        events = [_compact_boundary_event(), _result_event()]
        chat = _make_chat(events)

        assert chat._needs_orientation is False

        await stream_chat_events(set(), chat)

        assert chat._needs_orientation is True

    async def test_system_event_status_does_not_set_flag(self, mock_broadcast, mock_persist):
        """A SystemEvent with subtype 'status' does NOT set _needs_orientation."""
        from alpha_app.routes.streaming import stream_chat_events

        events = [_system_event("status"), _result_event()]
        chat = _make_chat(events)

        await stream_chat_events(set(), chat)

        assert chat._needs_orientation is False

    async def test_system_event_init_does_not_set_flag(self, mock_broadcast, mock_persist):
        """A SystemEvent with subtype 'init' does NOT set _needs_orientation."""
        from alpha_app.routes.streaming import stream_chat_events

        events = [_system_event("init"), _result_event()]
        chat = _make_chat(events)

        await stream_chat_events(set(), chat)

        assert chat._needs_orientation is False

    async def test_stream_event_does_not_set_flag(self, mock_broadcast, mock_persist):
        """A StreamEvent does not affect _needs_orientation."""
        from alpha_app.routes.streaming import stream_chat_events

        events = [_stream_event("Hello"), _result_event()]
        chat = _make_chat(events)

        await stream_chat_events(set(), chat)

        assert chat._needs_orientation is False

    async def test_assistant_event_does_not_set_flag(self, mock_broadcast, mock_persist):
        """An AssistantEvent does not affect _needs_orientation."""
        from alpha_app.routes.streaming import stream_chat_events

        events = [_assistant_event("Hello!"), _result_event()]
        chat = _make_chat(events)

        await stream_chat_events(set(), chat)

        assert chat._needs_orientation is False

    async def test_compact_boundary_mid_stream(self, mock_broadcast, mock_persist):
        """compact_boundary sets flag even when it appears between other events."""
        from alpha_app.routes.streaming import stream_chat_events

        events = [
            _stream_event("He"),
            _stream_event("llo"),
            _assistant_event("Hello"),
            _system_event("status"),
            _compact_boundary_event(),
            _stream_event("World"),
            _assistant_event("World"),
            _result_event(),
        ]
        chat = _make_chat(events)

        assert chat._needs_orientation is False

        await stream_chat_events(set(), chat)

        assert chat._needs_orientation is True

    async def test_multiple_system_events_only_compact_boundary_sets_flag(
        self, mock_broadcast, mock_persist
    ):
        """Multiple SystemEvents in a stream: only compact_boundary sets flag."""
        from alpha_app.routes.streaming import stream_chat_events

        events = [
            _system_event("init"),
            _system_event("status"),
            _system_event("session_id"),
            _result_event(),
        ]
        chat = _make_chat(events)

        await stream_chat_events(set(), chat)

        assert chat._needs_orientation is False

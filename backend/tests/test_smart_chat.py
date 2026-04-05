"""Tests for Smart Chat — the callback-based Chat.

Validates that Chat._on_claude_event correctly:
- Broadcasts WebSocket events from Claude stdout events
- Accumulates AssistantMessage parts progressively
- Finalizes and appends to chat.messages on ResultEvent
- Handles tool calls, tool results, thinking, text
"""

import asyncio
import json
import uuid

import pytest

from alpha_app import (
    AssistantEvent,
    ErrorEvent,
    Event,
    ResultEvent,
    StreamEvent,
    SystemEvent,
    UserEvent,
)
from alpha_app.chat import Chat, ConversationState, SuggestState
from alpha_app.models import AssistantMessage, UserMessage


# -- Helpers ------------------------------------------------------------------


def _stream(event_type: str, **kwargs) -> StreamEvent:
    """Build a StreamEvent from inner event dict."""
    inner = {"type": event_type, **kwargs}
    return StreamEvent(raw={"type": "stream_event", "event": inner}, inner=inner)


def _text_delta(text: str) -> StreamEvent:
    return _stream(
        "content_block_delta",
        index=1,
        delta={"type": "text_delta", "text": text},
    )


def _thinking_delta(text: str) -> StreamEvent:
    return _stream(
        "content_block_delta",
        index=0,
        delta={"type": "thinking_delta", "thinking": text},
    )


def _tool_start(tool_id: str, name: str) -> StreamEvent:
    return _stream(
        "content_block_start",
        index=2,
        content_block={"type": "tool_use", "id": tool_id, "name": name},
    )


def _assistant_event(content: list[dict]) -> AssistantEvent:
    return AssistantEvent(
        raw={"type": "assistant", "message": {"content": content}},
        content=content,
    )


def _result_event(session_id: str = "test-session", cost: float = 0.01) -> ResultEvent:
    return ResultEvent(
        raw={"type": "result"},
        session_id=session_id,
        cost_usd=cost,
        num_turns=1,
        duration_ms=1000,
        is_error=False,
    )


def _user_event(content: list[dict]) -> UserEvent:
    return UserEvent(
        raw={"type": "user", "message": {"content": content}},
        content=content,
    )


def _tool_result_event(tool_id: str, result: str) -> UserEvent:
    return UserEvent(
        raw={"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tool_id, "content": result, "is_error": False}
        ]}},
        content=[
            {"type": "tool_result", "tool_use_id": tool_id, "content": result, "is_error": False}
        ],
    )


class BroadcastCollector:
    """Mock broadcast callback that collects events."""

    def __init__(self):
        self.events: list[dict] = []

    async def __call__(self, event: dict) -> None:
        self.events.append(event)

    def by_type(self, event_type: str) -> list[dict]:
        return [e for e in self.events if e.get("type") == event_type]

    @property
    def types(self) -> list[str]:
        return [e.get("type", "?") for e in self.events]


# -- Tests --------------------------------------------------------------------


class TestSmartChatCallback:
    """Test Chat._on_claude_event."""

    @pytest.fixture
    def chat(self):
        c = Chat(id="test-chat")
        c.state = ConversationState.READY
        c.on_broadcast = BroadcastCollector()
        return c

    @pytest.fixture
    def broadcasts(self, chat):
        return chat.on_broadcast

    async def test_text_delta_broadcasts_and_accumulates(self, chat, broadcasts):
        await chat._on_claude_event(_text_delta("Hello"))
        await chat._on_claude_event(_text_delta(" world"))

        # Should broadcast two text-deltas
        assert broadcasts.by_type("text-delta") == [
            {"type": "text-delta", "chatId": "test-chat", "data": "Hello"},
            {"type": "text-delta", "chatId": "test-chat", "data": " world"},
        ]

        # Should accumulate into one text part
        assert chat._current_assistant is not None
        assert len(chat._current_assistant.parts) == 1
        assert chat._current_assistant.parts[0] == {"type": "text", "text": "Hello world"}

    async def test_thinking_delta_broadcasts_and_accumulates(self, chat, broadcasts):
        await chat._on_claude_event(_thinking_delta("Let me think"))
        await chat._on_claude_event(_thinking_delta(" about this"))

        assert len(broadcasts.by_type("thinking-delta")) == 2
        assert chat._current_assistant.parts[0] == {
            "type": "thinking",
            "thinking": "Let me think about this",
        }

    async def test_tool_use_start_broadcasts(self, chat, broadcasts):
        await chat._on_claude_event(_tool_start("tool-1", "Bash"))

        starts = broadcasts.by_type("tool-use-start")
        assert len(starts) == 1
        assert starts[0]["data"]["toolCallId"] == "tool-1"
        assert starts[0]["data"]["toolName"] == "Bash"

    async def test_assistant_event_accumulates_tool_call(self, chat, broadcasts):
        await chat._on_claude_event(_assistant_event([
            {"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {"command": "ls"}},
        ]))

        assert len(broadcasts.by_type("tool-call")) == 1
        assert chat._current_assistant is not None
        assert chat._current_assistant.parts[-1]["type"] == "tool-call"
        assert chat._current_assistant.parts[-1]["toolName"] == "Bash"

    async def test_tool_result_updates_tool_call_part(self, chat, broadcasts):
        # First, accumulate a tool call
        await chat._on_claude_event(_assistant_event([
            {"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {"command": "ls"}},
        ]))

        # Then the tool result arrives
        await chat._on_claude_event(_tool_result_event("tool-1", "file1.txt\nfile2.txt"))

        # The tool-call part should have the result
        assert chat._current_assistant.parts[-1]["result"] == "file1.txt\nfile2.txt"
        assert broadcasts.by_type("tool-result")[0]["data"]["result"] == "file1.txt\nfile2.txt"

    async def test_result_event_finalizes_message(self, chat, broadcasts):
        # Accumulate some text
        await chat._on_claude_event(_text_delta("Hello!"))

        # Message is in messages[] immediately (for late-joiner support)
        assert len(chat.messages) == 1
        assert chat.messages[0] is chat._current_assistant

        # ResultEvent finalizes
        await chat._on_claude_event(_result_event())

        # Still one message — same object, now with metadata
        assert len(chat.messages) == 1
        msg = chat.messages[0]
        assert isinstance(msg, AssistantMessage)
        assert msg.parts[0] == {"type": "text", "text": "Hello!"}

        # Accumulator should be cleared
        assert chat._current_assistant is None

        # State should transition
        assert chat.state == ConversationState.READY
        assert chat.suggest == SuggestState.ARMED

        # Should have broadcast assistant-message and chat-state.
        # Two chat-state broadcasts: spontaneous response detection (RESPONDING)
        # and result finalization (READY).
        assert len(broadcasts.by_type("assistant-message")) == 1
        assert len(broadcasts.by_type("chat-state")) == 2
        assert broadcasts.by_type("chat-state")[0]["data"]["state"] == "busy"  # RESPONDING
        assert broadcasts.by_type("chat-state")[1]["data"]["state"] == "idle"  # READY

    async def test_result_event_captures_session_uuid(self, chat, broadcasts):
        await chat._on_claude_event(_text_delta("Hi"))
        await chat._on_claude_event(_result_event(session_id="my-session-uuid"))

        assert chat.session_uuid == "my-session-uuid"

    async def test_full_turn_sequence(self, chat, broadcasts):
        """Simulate a full turn: thinking → text → result."""
        # Thinking
        await chat._on_claude_event(_thinking_delta("I should say hello"))

        # Text
        await chat._on_claude_event(_text_delta("Hello, "))
        await chat._on_claude_event(_text_delta("Jeffery!"))

        # Result
        await chat._on_claude_event(_result_event())

        # Check the assembled message
        assert len(chat.messages) == 1
        msg = chat.messages[0]
        assert len(msg.parts) == 2
        assert msg.parts[0] == {"type": "thinking", "thinking": "I should say hello"}
        assert msg.parts[1] == {"type": "text", "text": "Hello, Jeffery!"}

        # Check broadcast types in order
        assert "thinking-delta" in broadcasts.types
        assert "text-delta" in broadcasts.types
        assert "assistant-message" in broadcasts.types
        assert "chat-state" in broadcasts.types

    async def test_error_event_broadcasts(self, chat, broadcasts):
        await chat._on_claude_event(
            ErrorEvent(raw={}, message="subprocess died")
        )

        errors = broadcasts.by_type("error")
        assert len(errors) == 1
        assert errors[0]["data"] == "subprocess died"

    async def test_compact_boundary_resets_orientation(self, chat, broadcasts):
        chat._needs_orientation = False
        chat._injected_topics = {"alpha-app"}

        await chat._on_claude_event(
            SystemEvent(raw={}, subtype="compact_boundary")
        )

        assert chat._needs_orientation is True
        assert chat._injected_topics == set()

    async def test_empty_result_does_not_append(self, chat, broadcasts):
        """ResultEvent with no accumulated parts → nothing appended."""
        await chat._on_claude_event(_result_event())

        assert len(chat.messages) == 0
        assert len(broadcasts.by_type("assistant-message")) == 0

    async def test_multiple_turns_accumulate(self, chat, broadcasts):
        """Two sequential turns should produce two messages."""
        # Turn 1
        await chat._on_claude_event(_text_delta("First response"))
        await chat._on_claude_event(_result_event())

        # Turn 2
        await chat._on_claude_event(_text_delta("Second response"))
        await chat._on_claude_event(_result_event())

        assert len(chat.messages) == 2
        assert chat.messages[0].parts[0]["text"] == "First response"
        assert chat.messages[1].parts[0]["text"] == "Second response"

    async def test_human_turn_counter_increments_on_human_turn(self, chat, broadcasts):
        """_human_turn_count should increment once per completed human-initiated turn."""
        # Stub out the suggest dispatch so we don't need Ollama.
        fired: list[int] = []

        async def fake_suggest(user_text: str, assistant_text: str) -> None:
            fired.append(chat._human_turn_count)

        chat._post_turn_suggest = fake_suggest

        assert chat._human_turn_count == 0

        # Three human-initiated turns in a row.
        for i in range(3):
            chat.messages.append(
                UserMessage(id=f"u{i}", content=[{"type": "text", "text": f"msg {i}"}], source="human")
            )
            await chat._on_claude_event(_text_delta(f"reply {i}"))
            await chat._on_claude_event(_result_event())

        assert chat._human_turn_count == 3

    async def test_suggest_cadence_fires_on_1_4_7(self, chat, broadcasts):
        """N=3 cadence: suggest fires on turns 1, 4, 7, 10, ... (and nowhere else)."""
        fired_on: list[int] = []

        async def fake_suggest(user_text: str, assistant_text: str) -> None:
            fired_on.append(chat._human_turn_count)

        chat._post_turn_suggest = fake_suggest

        # Run 10 human turns and record which ones fired suggest.
        for i in range(10):
            chat.messages.append(
                UserMessage(id=f"u{i}", content=[{"type": "text", "text": f"msg {i}"}], source="human")
            )
            await chat._on_claude_event(_text_delta(f"reply {i}"))
            await chat._on_claude_event(_result_event())
            # asyncio.create_task runs the coroutine eagerly enough that by
            # the next event loop turn it's recorded. Give it a beat.
            await asyncio.sleep(0)

        assert fired_on == [1, 4, 7, 10]

    async def test_human_turn_counter_persists_round_trip(self):
        """to_data() and from_db() should preserve _human_turn_count."""
        chat = Chat(id="persist-test")
        chat._human_turn_count = 7

        data = chat.to_data()
        assert data["human_turn_count"] == 7

        restored = Chat.from_db("persist-test", updated_at=0.0, data=data)
        assert restored._human_turn_count == 7

    async def test_human_turn_counter_defaults_to_zero_on_missing_key(self):
        """Backfill: legacy chats without human_turn_count in JSONB load as 0."""
        legacy_data = {
            "session_uuid": "abc",
            "title": "old chat",
            "created_at": 0,
            "token_count": 1000,
            "context_window": 1_000_000,
            # no human_turn_count key
        }
        chat = Chat.from_db("legacy", updated_at=0.0, data=legacy_data)
        assert chat._human_turn_count == 0

    async def test_messages_to_wire(self, chat, broadcasts):
        """Serialize messages for the 'gimme the fucking chat' payload."""
        # Add a user message manually (as Chat.send() would)
        user_msg = UserMessage(id="u1", content=[{"type": "text", "text": "Hi"}])
        chat.messages.append(user_msg)

        # Add an assistant response via the callback
        await chat._on_claude_event(_text_delta("Hello!"))
        await chat._on_claude_event(_result_event())

        wire = chat.messages_to_wire()
        assert len(wire) == 2
        assert wire[0]["role"] == "user"
        assert wire[1]["role"] == "assistant"
        assert wire[1]["data"]["parts"][0]["text"] == "Hello!"

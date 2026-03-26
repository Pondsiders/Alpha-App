"""Tests for Chat.flush() dirty-bit persistence.

Validates that:
- Messages are born dirty and become clean after flush()
- flush() with no dirty messages returns 0 without touching the DB
- Messages loaded from Postgres are born clean
- flush() calls persist_chat in addition to writing messages

Tier 1: unit tests using patched DB (no Postgres required)
Tier 2: integration tests using real Postgres (marked @pytest.mark.integration)
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from alpha_app.chat import Chat, ConversationState
from alpha_app.models import AssistantMessage, UserMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool_mock(fetch_rows=None):
    """Build a mock asyncpg pool with async-context-manager acquire()."""
    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=fetch_rows or [])

    mock_txn = AsyncMock()
    mock_txn.__aenter__ = AsyncMock(return_value=None)
    mock_txn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_txn)

    mock_acquire = AsyncMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_acquire)

    return mock_pool, mock_conn


def _user_msg(text="Hello") -> UserMessage:
    return UserMessage(
        id=str(uuid.uuid4()),
        content=[{"type": "text", "text": text}],
    )


def _assistant_msg(text="Hi there") -> AssistantMessage:
    return AssistantMessage(
        id=str(uuid.uuid4()),
        parts=[{"type": "text", "text": text}],
    )


def _stream_event(text: str):
    from alpha_app import StreamEvent
    inner = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": text},
    }
    return StreamEvent(raw={"type": "stream_event", "event": inner}, inner=inner)


def _result_event():
    from alpha_app import ResultEvent
    return ResultEvent(
        raw={"type": "result"},
        session_id="test-session",
        cost_usd=0.001,
        num_turns=1,
        duration_ms=500,
        is_error=False,
    )


# ---------------------------------------------------------------------------
# Tier 1: Unit tests (patched DB, no Postgres)
# ---------------------------------------------------------------------------


class TestDirtyBit:
    """Messages are born dirty; flush() clears the flag."""

    def test_user_message_born_dirty(self):
        msg = _user_msg()
        assert msg._dirty is True

    def test_assistant_message_born_dirty(self):
        msg = _assistant_msg()
        assert msg._dirty is True

    async def test_user_message_clean_after_flush(self):
        chat = Chat(id="test-flush-user")
        msg = _user_msg("How's it going?")
        chat.messages.append(msg)

        pool_mock, _ = _make_pool_mock()
        with (
            patch("alpha_app.db.get_pool", return_value=pool_mock),
            patch("alpha_app.db.persist_chat", new_callable=AsyncMock),
        ):
            count = await chat.flush()

        assert count == 1
        assert msg._dirty is False

    async def test_assistant_message_clean_after_flush(self):
        chat = Chat(id="test-flush-asst")
        msg = _assistant_msg("Here's what I think")
        chat.messages.append(msg)

        pool_mock, _ = _make_pool_mock()
        with (
            patch("alpha_app.db.get_pool", return_value=pool_mock),
            patch("alpha_app.db.persist_chat", new_callable=AsyncMock),
        ):
            count = await chat.flush()

        assert count == 1
        assert msg._dirty is False

    async def test_flush_no_dirty_returns_zero(self):
        """flush() with no dirty messages is a no-op — returns 0, no DB calls."""
        chat = Chat(id="test-flush-nodirty")
        msg = _user_msg()
        msg._dirty = False
        chat.messages.append(msg)

        mock_get_pool = MagicMock()
        mock_persist = AsyncMock()
        with (
            patch("alpha_app.db.get_pool", mock_get_pool),
            patch("alpha_app.db.persist_chat", mock_persist),
        ):
            count = await chat.flush()

        assert count == 0
        mock_get_pool.assert_not_called()
        mock_persist.assert_not_called()

    async def test_flush_calls_persist_chat(self):
        """flush() with dirty messages always calls persist_chat."""
        chat = Chat(id="test-flush-persist")
        chat.messages.append(_user_msg("Test"))

        pool_mock, _ = _make_pool_mock()
        mock_persist = AsyncMock()
        with (
            patch("alpha_app.db.get_pool", return_value=pool_mock),
            patch("alpha_app.db.persist_chat", mock_persist),
        ):
            await chat.flush()

        mock_persist.assert_awaited_once_with(chat)


class TestLoadMessages:
    """Messages loaded from Postgres are born clean."""

    async def test_loaded_messages_are_clean(self):
        """load_messages() hydrates chat.messages with _dirty=False."""
        chat = Chat(id="test-load-clean")

        rows = [
            {
                "role": "user",
                "data": {
                    "id": "u1",
                    "content": [{"type": "text", "text": "Hey"}],
                    "source": "human",
                    "timestamp": None,
                },
            },
            {
                "role": "assistant",
                "data": {
                    "id": "a1",
                    "parts": [{"type": "text", "text": "Hello"}],
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_creation_tokens": 0,
                    "cache_read_tokens": 0,
                    "context_window": 0,
                },
            },
        ]

        pool_mock, _ = _make_pool_mock(fetch_rows=rows)
        with patch("alpha_app.db.get_pool", new_callable=AsyncMock, return_value=pool_mock):
            await chat.load_messages()

        assert len(chat.messages) == 2
        for msg in chat.messages:
            assert msg._dirty is False, (
                f"{type(msg).__name__} loaded from DB should be born clean"
            )


# ---------------------------------------------------------------------------
# Tier 2: Integration tests (real Postgres)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFlushIntegration:
    """End-to-end persistence through flush() and load_messages()."""

    @pytest.fixture(autouse=True)
    async def db_pool(self):
        """Initialize and tear down the DB pool for each test."""
        from alpha_app.db import init_pool, close_pool
        await init_pool()
        yield
        await close_pool()

    async def test_flush_round_trip(self):
        """Append a UserMessage, flush, load_messages — data matches."""
        from alpha_app.db import get_pool

        chat_id = f"test-{uuid.uuid4().hex[:8]}"
        chat = Chat(id=chat_id)
        msg = _user_msg("Integration test message")
        chat.messages.append(msg)

        try:
            await chat.flush()
            assert msg._dirty is False

            fresh = Chat(id=chat_id)
            await fresh.load_messages()

            assert len(fresh.messages) == 1
            loaded = fresh.messages[0]
            assert isinstance(loaded, UserMessage)
            assert loaded._dirty is False
            assert loaded.content == msg.content
        finally:
            pool = get_pool()
            await pool.execute("DELETE FROM app.messages WHERE chat_id = $1", chat_id)
            await pool.execute("DELETE FROM app.chats WHERE id = $1", chat_id)

    async def test_full_turn_persists_both_messages(self):
        """After a full turn, both UserMessage and AssistantMessage appear in app.messages."""
        from alpha_app.db import get_pool

        chat_id = f"test-{uuid.uuid4().hex[:8]}"
        chat = Chat(id=chat_id)
        chat.state = ConversationState.READY
        chat.on_broadcast = AsyncMock()

        try:
            # Append user message (as turn_smart would)
            user_msg = _user_msg("Tell me something")
            chat.messages.append(user_msg)

            # Simulate Claude streaming a response and finishing
            await chat._on_claude_event(_stream_event("Here's something."))
            await chat._on_claude_event(_result_event())

            rows = await get_pool().fetch(
                "SELECT role, data FROM app.messages WHERE chat_id = $1 ORDER BY ordinal",
                chat_id,
            )
            assert len(rows) == 2
            assert rows[0]["role"] == "user"
            assert rows[1]["role"] == "assistant"
        finally:
            pool = get_pool()
            await pool.execute("DELETE FROM app.messages WHERE chat_id = $1", chat_id)
            await pool.execute("DELETE FROM app.chats WHERE id = $1", chat_id)

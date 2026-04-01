"""Tests for enrobe.py — message enrichment pipeline.

Tests the orientation injection wired through the _needs_orientation flag:
  - Orientation blocks injected when flag is True
  - No orientation blocks when flag is False
  - Flag cleared after enrobe runs with it True
  - Block order: orientation first, then timestamp, then user content
  - Orientation blocks are proper content block dicts
"""

from unittest.mock import AsyncMock, patch

import pytest

from alpha_app.routes.enrobe import enrobe, EnrobeResult


# ---------------------------------------------------------------------------
# Minimal Chat stub — just needs _needs_orientation and id
# ---------------------------------------------------------------------------


class ChatStub:
    """Minimal stand-in for Chat. Only the attributes enrobe touches."""

    def __init__(self, *, needs_orientation: bool = True, chat_id: str = "test-abc123"):
        self.id = chat_id
        self._needs_orientation = needs_orientation
        self._pending_intro = None


# ---------------------------------------------------------------------------
# Controlled orientation data — deterministic, minimal
# ---------------------------------------------------------------------------

FAKE_HERE = "## Here\n\nYou are in Alpha — test mode on `testhost`."

FAKE_ORIENTATION = {
    "yesterday": None,
    "last_night": None,
    "letter": None,
    "today_so_far": None,
    "here": FAKE_HERE,
    "context_files": None,
    "context_available": None,
    "events": None,
    "todos": None,
}


def _mock_orientation(**overrides):
    """Create an AsyncMock for fetch_all_orientation with optional overrides."""
    data = {**FAKE_ORIENTATION, **overrides}
    return AsyncMock(return_value=data)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEnrobeOrientation:
    """Tests for orientation injection via _needs_orientation flag."""

    @pytest.fixture(autouse=True)
    def mock_recall(self):
        """Mock recall_memories to return [] — these tests are about orientation."""
        with patch(
            "alpha_app.routes.enrobe.recall",
            AsyncMock(return_value=[]),
        ):
            yield

    @pytest.fixture
    def user_content(self):
        """A simple user message content block list."""
        return [{"type": "text", "text": "Hello, world!"}]

    async def test_orientation_no_longer_in_enrobe(self, user_content):
        """Orientation now lives in system prompt, not in enrobe output."""
        chat = ChatStub(needs_orientation=True)

        result = await enrobe(user_content, chat=chat)

        # Should have: timestamp + user content only (2 blocks)
        # No orientation — it's in the system prompt now
        assert len(result.content) == 2
        texts = [b["text"] for b in result.content]
        assert not any("## Here" in t for t in texts)

    async def test_no_orientation_when_flag_false(self, user_content):
        """When _needs_orientation is False, no orientation blocks appear."""
        chat = ChatStub(needs_orientation=False)

        result = await enrobe(user_content, chat=chat)

        # Should have: timestamp + user content only (2 blocks)
        assert len(result.content) == 2

        # No orientation content
        texts = [b["text"] for b in result.content]
        assert not any("## Here" in t for t in texts)

    async def test_flag_cleared_after_enrobe(self, user_content):
        """The _needs_orientation flag is cleared by enrobe even though orientation is in system prompt."""
        chat = ChatStub(needs_orientation=True)
        assert chat._needs_orientation is True

        await enrobe(user_content, chat=chat)

        assert chat._needs_orientation is False

    async def test_flag_stays_false_when_already_false(self, user_content):
        """Flag remains False if it was already False."""
        chat = ChatStub(needs_orientation=False)

        result = await enrobe(user_content, chat=chat)

        assert chat._needs_orientation is False

    async def test_block_order_timestamp_then_user(self, user_content):
        """Order: timestamp, then user content. No orientation (it's in system prompt now)."""
        chat = ChatStub(needs_orientation=True)

        result = await enrobe(user_content, chat=chat)

        blocks = result.content

        # First block is the timestamp
        assert blocks[0]["text"].startswith("[Sent ")
        assert blocks[0]["text"].endswith("]")

        # Second block is the user content
        assert blocks[1]["text"] == "Hello, world!"

    async def test_timestamp_always_present(self, user_content):
        """Timestamp block is always present, before user content."""
        for needs_orientation in (True, False):
            chat = ChatStub(needs_orientation=needs_orientation)

            result = await enrobe(user_content, chat=chat)

            assert result.content[-2]["text"].startswith("[Sent ")
            assert result.content[-2]["type"] == "text"

    async def test_enrobe_returns_enrobe_result(self, user_content):
        """enrobe returns an EnrobeResult with content."""
        chat = ChatStub(needs_orientation=True)

        result = await enrobe(user_content, chat=chat)

        assert isinstance(result, EnrobeResult)
        assert isinstance(result.content, list)
        # Events are now broadcast via callback, not returned in the list.
        assert result.events == []

    async def test_enrobe_broadcasts_via_callback(self, user_content):
        """When broadcast_fn is provided, enrobe broadcasts at each step."""
        chat = ChatStub(needs_orientation=True)
        broadcasts = []

        async def capture(event):
            broadcasts.append(event)

        await enrobe(user_content, chat=chat, broadcast_fn=capture)

        assert any(e["type"] == "user-message" for e in broadcasts)

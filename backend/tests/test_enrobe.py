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
            "alpha_app.routes.enrobe.recall_memories",
            AsyncMock(return_value=[]),
        ):
            yield

    @pytest.fixture
    def user_content(self):
        """A simple user message content block list."""
        return [{"type": "text", "text": "Hello, world!"}]

    async def test_orientation_injected_when_flag_true(self, user_content):
        """When _needs_orientation is True, orientation blocks appear in output."""
        chat = ChatStub(needs_orientation=True)

        with patch("alpha_app.routes.enrobe.fetch_all_orientation", _mock_orientation()):
            result = await enrobe(user_content, chat=chat)

        # Should have: orientation (at least 1 block) + timestamp + user content
        assert len(result.content) >= 3

        # The orientation block should contain "## Here"
        texts = [b["text"] for b in result.content]
        assert any("## Here" in t for t in texts)

    async def test_no_orientation_when_flag_false(self, user_content):
        """When _needs_orientation is False, no orientation blocks appear."""
        chat = ChatStub(needs_orientation=False)

        result = await enrobe(user_content, chat=chat)

        # Should have: timestamp + user content only (2 blocks)
        assert len(result.content) == 2

        # No orientation content
        texts = [b["text"] for b in result.content]
        assert not any("## Here" in t for t in texts)

    async def test_flag_cleared_after_orientation(self, user_content):
        """The _needs_orientation flag is set to False after enrobe injects orientation."""
        chat = ChatStub(needs_orientation=True)
        assert chat._needs_orientation is True

        with patch("alpha_app.routes.enrobe.fetch_all_orientation", _mock_orientation()):
            await enrobe(user_content, chat=chat)

        assert chat._needs_orientation is False

    async def test_flag_stays_false_when_already_false(self, user_content):
        """Flag remains False if it was already False."""
        chat = ChatStub(needs_orientation=False)

        result = await enrobe(user_content, chat=chat)

        assert chat._needs_orientation is False

    async def test_block_order_orientation_timestamp_user(self, user_content):
        """Order: orientation first, then timestamp, then user content."""
        chat = ChatStub(needs_orientation=True)

        with patch("alpha_app.routes.enrobe.fetch_all_orientation", _mock_orientation()):
            result = await enrobe(user_content, chat=chat)

        blocks = result.content

        # First block is orientation (here)
        assert "## Here" in blocks[0]["text"]

        # Second-to-last block is the timestamp
        assert blocks[-2]["text"].startswith("[Sent ")
        assert blocks[-2]["text"].endswith("]")

        # Last block is always the user content
        assert blocks[-1]["text"] == "Hello, world!"

    async def test_orientation_blocks_are_proper_content_dicts(self, user_content):
        """Orientation blocks must be {"type": "text", "text": "..."} dicts."""
        chat = ChatStub(needs_orientation=True)

        with patch("alpha_app.routes.enrobe.fetch_all_orientation", _mock_orientation()):
            result = await enrobe(user_content, chat=chat)

        # Check all blocks (orientation, timestamp, user content) are proper dicts
        for block in result.content:
            assert isinstance(block, dict)
            assert set(block.keys()) == {"type", "text"}
            assert block["type"] == "text"
            assert isinstance(block["text"], str)
            assert len(block["text"]) > 0

    async def test_second_call_has_no_orientation(self, user_content):
        """After the first enrobe clears the flag, the second call has no orientation."""
        chat = ChatStub(needs_orientation=True)

        with patch("alpha_app.routes.enrobe.fetch_all_orientation", _mock_orientation()):
            first_result = await enrobe(user_content, chat=chat)

        # First call has orientation
        assert len(first_result.content) >= 3

        # Second call — flag is now False
        second_result = await enrobe(user_content, chat=chat)

        # Second call has no orientation
        assert len(second_result.content) == 2
        texts = [b["text"] for b in second_result.content]
        assert not any("## Here" in t for t in texts)

    async def test_timestamp_always_present(self, user_content):
        """Timestamp block is always present, just before user content."""
        for needs_orientation in (True, False):
            chat = ChatStub(needs_orientation=needs_orientation)

            with patch("alpha_app.routes.enrobe.fetch_all_orientation", _mock_orientation()):
                result = await enrobe(user_content, chat=chat)

            # Second-to-last block is always the timestamp
            assert result.content[-2]["text"].startswith("[Sent ")
            assert result.content[-2]["type"] == "text"

    async def test_enrobe_returns_enrobe_result(self, user_content):
        """enrobe returns an EnrobeResult with content and events."""
        chat = ChatStub(needs_orientation=True)

        with patch("alpha_app.routes.enrobe.fetch_all_orientation", _mock_orientation()):
            result = await enrobe(user_content, chat=chat)

        assert isinstance(result, EnrobeResult)
        assert isinstance(result.content, list)
        assert isinstance(result.events, list)
        # Should still have the timestamp event
        assert any(e["type"] == "enrichment-timestamp" for e in result.events)

    async def test_full_orientation_with_all_sources(self, user_content):
        """When all sources are present, all orientation blocks appear in order."""
        chat = ChatStub(needs_orientation=True)

        mock = _mock_orientation(
            yesterday="## Friday, February 27, 2026\n\nBig day.",
            last_night="## Friday night\n\nQuiet.",
            letter="## Letter from last night\n\nHey.",
            today_so_far="## Today so far\n\nGood morning.",
            events="**Tomorrow**\n\u2022 3:30 PM: Meeting",
            todos="*Pondside*\n\u2022 Build things",
        )

        with patch("alpha_app.routes.enrobe.fetch_all_orientation", mock):
            result = await enrobe(user_content, chat=chat)

        texts = [b["text"] for b in result.content]

        # Capsules come before here (new block order)
        here_idx = next(i for i, t in enumerate(texts) if "## Here" in t)
        yesterday_idx = next(i for i, t in enumerate(texts) if "February 27" in t)
        assert yesterday_idx < here_idx

        # Events and todos at the end (before timestamp + user)
        events_idx = next(i for i, t in enumerate(texts) if "## Events" in t)
        todos_idx = next(i for i, t in enumerate(texts) if "## Todos" in t)
        assert events_idx < todos_idx
        assert todos_idx < len(texts) - 2  # Before timestamp + user

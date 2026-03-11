"""Tests for enrobe.py — message enrichment pipeline.

Tests the orientation injection wired through the _needs_orientation flag:
  - Orientation blocks injected when flag is True
  - No orientation blocks when flag is False
  - Flag cleared after enrobe runs with it True
  - Block order: timestamp first, then orientation, then user content
  - Orientation blocks are proper content block dicts
"""

from unittest.mock import patch

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
# Controlled get_here return value — deterministic across environments
# ---------------------------------------------------------------------------

FAKE_HERE = "[Narrator] You are in Alpha v0.0.0-test running on bare metal on `testhost`."


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEnrobeOrientation:
    """Tests for orientation injection via _needs_orientation flag."""

    @pytest.fixture
    def user_content(self):
        """A simple user message content block list."""
        return [{"type": "text", "text": "Hello, world!"}]

    async def test_orientation_injected_when_flag_true(self, user_content):
        """When _needs_orientation is True, orientation blocks appear in output."""
        chat = ChatStub(needs_orientation=True)

        with patch("alpha_app.routes.enrobe.get_here", return_value=FAKE_HERE):
            result = await enrobe(user_content, chat=chat)

        # Should have: timestamp + orientation (at least 1 block) + user content
        assert len(result.content) >= 3

        # The orientation block should contain the "[Narrator]" header
        texts = [b["text"] for b in result.content]
        assert any("[Narrator]" in t for t in texts)
        assert any(FAKE_HERE in t for t in texts)

    async def test_no_orientation_when_flag_false(self, user_content):
        """When _needs_orientation is False, no orientation blocks appear."""
        chat = ChatStub(needs_orientation=False)

        result = await enrobe(user_content, chat=chat)

        # Should have: timestamp + user content only (2 blocks)
        assert len(result.content) == 2

        # No orientation content
        texts = [b["text"] for b in result.content]
        assert not any("[Narrator]" in t for t in texts)

    async def test_flag_cleared_after_orientation(self, user_content):
        """The _needs_orientation flag is set to False after enrobe injects orientation."""
        chat = ChatStub(needs_orientation=True)
        assert chat._needs_orientation is True

        with patch("alpha_app.routes.enrobe.get_here", return_value=FAKE_HERE):
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

        with patch("alpha_app.routes.enrobe.get_here", return_value=FAKE_HERE):
            result = await enrobe(user_content, chat=chat)

        blocks = result.content

        # First block is orientation (here)
        assert "[Narrator]" in blocks[0]["text"]

        # Second-to-last block is the timestamp
        assert blocks[-2]["text"].startswith("[Sent ")
        assert blocks[-2]["text"].endswith("]")

        # Last block is always the user content
        assert blocks[-1]["text"] == "Hello, world!"

    async def test_orientation_blocks_are_proper_content_dicts(self, user_content):
        """Orientation blocks must be {"type": "text", "text": "..."} dicts."""
        chat = ChatStub(needs_orientation=True)

        with patch("alpha_app.routes.enrobe.get_here", return_value=FAKE_HERE):
            result = await enrobe(user_content, chat=chat)

        # Check all blocks (timestamp, orientation, user content) are proper dicts
        for block in result.content:
            assert isinstance(block, dict)
            assert set(block.keys()) == {"type", "text"}
            assert block["type"] == "text"
            assert isinstance(block["text"], str)
            assert len(block["text"]) > 0

    async def test_second_call_has_no_orientation(self, user_content):
        """After the first enrobe clears the flag, the second call has no orientation."""
        chat = ChatStub(needs_orientation=True)

        with patch("alpha_app.routes.enrobe.get_here", return_value=FAKE_HERE):
            first_result = await enrobe(user_content, chat=chat)

        # First call has orientation
        assert len(first_result.content) >= 3

        # Second call — flag is now False
        second_result = await enrobe(user_content, chat=chat)

        # Second call has no orientation
        assert len(second_result.content) == 2
        texts = [b["text"] for b in second_result.content]
        assert not any("[Narrator]" in t for t in texts)

    async def test_timestamp_always_present(self, user_content):
        """Timestamp is always the first block regardless of orientation flag."""
        for needs_orientation in (True, False):
            chat = ChatStub(needs_orientation=needs_orientation)

            with patch("alpha_app.routes.enrobe.get_here", return_value=FAKE_HERE):
                result = await enrobe(user_content, chat=chat)

            # First block is always the timestamp
            assert result.content[0]["text"].startswith("[")
            assert result.content[0]["type"] == "text"

    async def test_enrobe_returns_enrobe_result(self, user_content):
        """enrobe returns an EnrobeResult with content and events."""
        chat = ChatStub(needs_orientation=True)

        with patch("alpha_app.routes.enrobe.get_here", return_value=FAKE_HERE):
            result = await enrobe(user_content, chat=chat)

        assert isinstance(result, EnrobeResult)
        assert isinstance(result.content, list)
        assert isinstance(result.events, list)
        # Should still have the timestamp event
        assert any(e["type"] == "enrichment-timestamp" for e in result.events)

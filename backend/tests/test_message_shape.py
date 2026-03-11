"""test_message_shape.py — Golden reference tests for user message content blocks.

The Duckpond capture IS the spec. These tests assert that the content blocks
sent to Claude match the shape and content of a real Alpha context window.

All synthetic data comes from golden_reference.py (derived from capture
20260311_125224_063527). The tests provide deterministic inputs through mocks
and assert the output matches first_turn_blocks() and normal_turn_blocks().

Failures show exactly which blocks are missing or misordered — each failure
is a TODO for enrobe.py.

Status: RED. Enrobe currently only implements timestamp + here orientation.
The test encodes the target; the implementation catches up.

Two shapes:
    first_turn  — orientation + memories + timestamp + user message (18 blocks)
    normal_turn — intro + memories + timestamp + user message (6 blocks)
"""

from unittest.mock import patch

import pytest

from alpha_app.routes.enrobe import enrobe
from tests.fixtures.golden_reference import (
    # Orientation source data (first turn only)
    CAPSULE_YESTERDAY,
    CAPSULE_LAST_NIGHT,
    LETTER,
    TODAY_SO_FAR,
    HERE,
    CONTEXT_FILES,
    CONTEXT_AVAILABLE,
    EVENTS,
    TODOS,
    # Memory source data
    MEMORIES_FIRST_TURN,
    MEMORIES_NORMAL_TURN,
    # Intro source data (normal turns)
    INTRO_SPEAKS,
    # Timestamps
    TIMESTAMP_FIRST,
    TIMESTAMP_NORMAL,
    # User messages
    USER_MESSAGE_FIRST,
    USER_MESSAGE_NORMAL,
    # Expected output builders
    first_turn_blocks,
    normal_turn_blocks,
)


# -- Minimal Chat stub -------------------------------------------------------


class ChatStub:
    """Minimal stand-in for Chat. Only the attributes enrobe touches."""

    def __init__(self, *, needs_orientation: bool = True, chat_id: str = "test-golden-001"):
        self.id = chat_id
        self._needs_orientation = needs_orientation


# -- First turn tests ---------------------------------------------------------


class TestFirstTurnShape:
    """First turn of a context window — the full orientation spread.

    Expected block order (18 blocks):
        capsules(2) -> letter -> today -> here -> context files(4) ->
        context index -> events -> todos -> memories(4) -> timestamp ->
        user message

    The user message is ALWAYS the last block. Everything before it is
    enrichment that enrobe adds.
    """

    @pytest.fixture
    def enrobe_first_turn(self):
        """Run enrobe for a first-turn message with deterministic mocks."""
        async def _run():
            chat = ChatStub(needs_orientation=True)
            content = [{"type": "text", "text": USER_MESSAGE_FIRST}]

            with (
                patch(
                    "alpha_app.routes.enrobe._format_timestamp",
                    return_value="Wed Mar 11 2026, 12:25 PM",
                ),
                patch(
                    "alpha_app.routes.enrobe.get_here",
                    return_value=HERE,
                ),
            ):
                result = await enrobe(content, chat=chat)

            return result, chat

        return _run

    async def test_block_count(self, enrobe_first_turn):
        """First turn should produce exactly 18 content blocks."""
        result, _ = await enrobe_first_turn()
        expected = first_turn_blocks()
        assert len(result.content) == len(expected), (
            f"Expected {len(expected)} blocks, got {len(result.content)}.\n"
            f"Missing: orientation data, memories, or both.\n"
            f"Got types: {[b['text'][:40] for b in result.content]}"
        )

    async def test_full_match(self, enrobe_first_turn):
        """Every content block must match the golden reference exactly."""
        result, _ = await enrobe_first_turn()
        expected = first_turn_blocks()

        # Block-by-block comparison for clear diffs
        for i, (actual, exp) in enumerate(zip(result.content, expected)):
            assert actual == exp, (
                f"Block {i} mismatch:\n"
                f"  expected: {exp['text'][:100]!r}\n"
                f"  actual:   {actual['text'][:100]!r}"
            )

        # Also catch length mismatch (zip stops at shorter)
        assert len(result.content) == len(expected)

    async def test_user_message_is_last(self, enrobe_first_turn):
        """Jeffery's message is ALWAYS the last block."""
        result, _ = await enrobe_first_turn()
        assert result.content[-1] == {"type": "text", "text": USER_MESSAGE_FIRST}

    async def test_orientation_flag_cleared(self, enrobe_first_turn):
        """_needs_orientation should be False after the first turn."""
        _, chat = await enrobe_first_turn()
        assert chat._needs_orientation is False


# -- Normal turn tests --------------------------------------------------------


class TestNormalTurnShape:
    """Normal (non-first) turn — intro, memories, timestamp, user message.

    Expected block order (6 blocks):
        intro -> memories(3) -> timestamp -> user message

    No orientation blocks. The user message is ALWAYS the last block.
    """

    @pytest.fixture
    def enrobe_normal_turn(self):
        """Run enrobe for a normal-turn message with deterministic mocks."""
        async def _run():
            chat = ChatStub(needs_orientation=False)
            content = [{"type": "text", "text": USER_MESSAGE_NORMAL}]

            with patch(
                "alpha_app.routes.enrobe._format_timestamp",
                return_value="Wed Mar 11 2026, 12:32 PM",
            ):
                result = await enrobe(content, chat=chat)

            return result, chat

        return _run

    async def test_block_count(self, enrobe_normal_turn):
        """Normal turn should produce exactly 6 content blocks."""
        result, _ = await enrobe_normal_turn()
        expected = normal_turn_blocks()
        assert len(result.content) == len(expected), (
            f"Expected {len(expected)} blocks, got {len(result.content)}.\n"
            f"Missing: intro suggestions, memories, or both.\n"
            f"Got types: {[b['text'][:40] for b in result.content]}"
        )

    async def test_full_match(self, enrobe_normal_turn):
        """Every content block must match the golden reference exactly."""
        result, _ = await enrobe_normal_turn()
        expected = normal_turn_blocks()

        for i, (actual, exp) in enumerate(zip(result.content, expected)):
            assert actual == exp, (
                f"Block {i} mismatch:\n"
                f"  expected: {exp['text'][:100]!r}\n"
                f"  actual:   {actual['text'][:100]!r}"
            )

        assert len(result.content) == len(expected)

    async def test_user_message_is_last(self, enrobe_normal_turn):
        """Jeffery's message is ALWAYS the last block."""
        result, _ = await enrobe_normal_turn()
        assert result.content[-1] == {"type": "text", "text": USER_MESSAGE_NORMAL}

    async def test_no_orientation_blocks(self, enrobe_normal_turn):
        """Normal turns should have no orientation blocks."""
        result, _ = await enrobe_normal_turn()
        texts = [b["text"] for b in result.content]
        assert not any("[Narrator]" in t for t in texts)
        assert not any("## Here" in t for t in texts)

    async def test_orientation_flag_stays_false(self, enrobe_normal_turn):
        """_needs_orientation should remain False on normal turns."""
        _, chat = await enrobe_normal_turn()
        assert chat._needs_orientation is False

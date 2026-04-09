"""Tests for orientation.py — context assembly for the system prompt.

Tests assemble_orientation() — pure assembly, no I/O.
"""

import pytest

from alpha_app.orientation import assemble_orientation, get_here


# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

HERE = "[Narrator] You are in Alpha v1.0.0 running in a Docker container on `primer`."

DIARY_YESTERDAY = "## Wed Apr 8 2026\n\n[10:00 PM]\n\nThe continuity system was born."
DIARY_TODAY = "## Thu Apr 9 2026 (so far)\n\n[10:18 AM]\n\nMorning session."

CONTEXT_FILES = [
    {"label": "ALPHA.md", "content": "# Alpha-Home — Living Document\n\nYour house."},
    {"label": "Barn/Duckpond/ALPHA.md", "content": "# Duckpond\n\nThe sovereign chat app."},
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAssembleOrientation:

    def test_full_orientation_all_sources(self):
        """All sources present. Correct number of blocks, correct order."""
        context_available = (
            "## Context available\n\n"
            "**BLOCKING REQUIREMENT:** Read the file BEFORE proceeding."
        )

        diary_yesterday = "## Wed Mar 10 2026\n\n[10:00 PM]\n\nThe monorepo day."
        diary_today = "## Thu Mar 11 2026 (so far)\n\n[8:00 AM]\n\nMoving day."

        result = assemble_orientation(
            here=HERE,
            diary_yesterday=DIARY_YESTERDAY,
            diary_today=DIARY_TODAY,
            context_files=CONTEXT_FILES,
            context_available=context_available,
        )

        assert isinstance(result, list)
        assert all(isinstance(b, dict) for b in result)
        assert all(b["type"] == "text" for b in result)

        # Count: 1 diary (wraps yesterday + today) + 1 here +
        #        2 context files + 1 context_available = 5
        assert len(result) == 5

        texts = [b["text"] for b in result]

        # Verify order: diary → here → context files → context available
        assert "# Diary" in texts[0]
        assert "Wed Apr 8" in texts[0]
        assert "Thu Apr 9" in texts[0]
        assert "[Narrator]" in texts[1]
        assert "## Context: ALPHA.md" in texts[2]
        assert "## Context: Barn/Duckpond/ALPHA.md" in texts[3]
        assert "## Context available" in texts[4]

    def test_partial_sources(self):
        """Some sources present, others missing."""
        result = assemble_orientation(
            here=HERE,
            diary_yesterday=DIARY_YESTERDAY,
        )

        assert len(result) == 2
        texts = [b["text"] for b in result]
        assert "# Diary" in texts[0]
        assert "[Narrator]" in texts[1]

    def test_here_only(self):
        """Just here, nothing else."""
        result = assemble_orientation(here=HERE)
        assert len(result) == 1
        assert "[Narrator]" in result[0]["text"]

    def test_empty_context_files(self):
        """Empty context_files list treated as absent."""
        result = assemble_orientation(here=HERE, context_files=[])
        assert len(result) == 1

    def test_none_sources_skipped(self):
        """None sources don't produce empty blocks."""
        result = assemble_orientation(
            here=HERE,
            diary_yesterday=None,
            diary_today=None,
            context_files=None,
            context_available=None,
        )
        assert len(result) == 1
        assert "[Narrator]" in result[0]["text"]


class TestGetHere:

    def test_returns_narrator_string(self):
        result = get_here()
        assert "[Narrator]" in result
        assert "Alpha" in result

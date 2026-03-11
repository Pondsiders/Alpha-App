"""Tests for orientation.py — assemble_orientation and check_venue_change."""

import pytest

from alpha_app.orientation import assemble_orientation, check_venue_change


# -- assemble_orientation -----------------------------------------------------


def test_orientation_minimal():
    """Only `here` returns a single block with ## Here header."""
    blocks = assemble_orientation(here="Alpha v1.0.0 on primer")
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"
    assert blocks[0]["text"] == "## Here\n\nAlpha v1.0.0 on primer"


def test_orientation_full():
    """All args produce blocks in the correct order."""
    blocks = assemble_orientation(
        here="Alpha v1.0.0 on primer",
        yesterday="## Yesterday\n\nYesterday stuff",
        last_night="## Last Night\n\nLast night stuff",
        letter="## Letter\n\nDear Alpha...",
        today_so_far="## Today So Far\n\nMorning things",
        weather="Sunny, 72°F",
        context_files=[{"label": "Notes.md", "content": "Some notes"}],
        events="Meeting at 2pm",
        todos="Buy groceries",
    )
    assert len(blocks) == 9
    assert blocks[0]["text"].startswith("## Here")
    assert blocks[1]["text"] == "## Yesterday\n\nYesterday stuff"
    assert blocks[2]["text"] == "## Last Night\n\nLast night stuff"
    assert blocks[3]["text"] == "## Letter\n\nDear Alpha..."
    assert blocks[4]["text"] == "## Today So Far\n\nMorning things"
    assert blocks[5]["text"] == "Sunny, 72°F"
    assert blocks[6]["text"] == "## Context: Notes.md\n\nSome notes"
    assert blocks[7]["text"] == "## Events\n\nMeeting at 2pm"
    assert blocks[8]["text"] == "## Todos\n\nBuy groceries"


def test_orientation_partial():
    """Some args produce only those blocks, in order."""
    blocks = assemble_orientation(
        here="Alpha v1.0.0",
        yesterday="## Yesterday\n\nYesterday",
        todos="Buy milk",
    )
    assert len(blocks) == 3
    assert blocks[0]["text"].startswith("## Here")
    assert "Yesterday" in blocks[1]["text"]
    assert blocks[2]["text"] == "## Todos\n\nBuy milk"


def test_orientation_none_sources_skipped():
    """None sources are silently omitted."""
    blocks = assemble_orientation(
        here="Alpha v1.0.0",
        yesterday=None,
        weather=None,
        todos=None,
    )
    assert len(blocks) == 1
    assert blocks[0]["text"].startswith("## Here")


def test_orientation_empty_string_sources_skipped():
    """Empty string sources are treated as absent."""
    blocks = assemble_orientation(
        here="Alpha v1.0.0",
        yesterday="",
        weather="",
        events="",
    )
    assert len(blocks) == 1


def test_orientation_empty_here_skipped():
    """Empty here string produces no block."""
    blocks = assemble_orientation(here="")
    assert len(blocks) == 0


def test_orientation_context_files_each_get_header():
    """Multiple context files each get their own block with ## Context: label."""
    blocks = assemble_orientation(
        here="Alpha v1.0.0",
        context_files=[
            {"label": "Alpha.md", "content": "Alpha notes"},
            {"label": "KERNEL.md", "content": "Kernel notes"},
        ],
    )
    assert len(blocks) == 3
    assert blocks[1]["text"] == "## Context: Alpha.md\n\nAlpha notes"
    assert blocks[2]["text"] == "## Context: KERNEL.md\n\nKernel notes"


def test_orientation_empty_context_files_skipped():
    """Empty context_files list produces no extra blocks."""
    blocks = assemble_orientation(here="Alpha v1.0.0", context_files=[])
    assert len(blocks) == 1


# -- check_venue_change -------------------------------------------------------


def test_venue_changed():
    """Returns a text block when venues differ."""
    result = check_venue_change(
        current="Alpha v1.0.0 on primer, Docker, branch: main",
        previous="Alpha v1.0.0 on primer, bare metal, branch: main",
    )
    assert result is not None
    assert result["type"] == "text"
    assert "bare metal" in result["text"]
    assert "Docker" in result["text"]


def test_venue_unchanged():
    """Returns None when current and previous are the same."""
    result = check_venue_change(
        current="Alpha v1.0.0 on primer, Docker",
        previous="Alpha v1.0.0 on primer, Docker",
    )
    assert result is None


def test_venue_no_previous():
    """Returns None when previous is None (first window, no comparison)."""
    result = check_venue_change(
        current="Alpha v1.0.0 on primer",
        previous=None,
    )
    assert result is None


def test_venue_branch_change():
    """Returns a notice when the git branch changes."""
    result = check_venue_change(
        current="Alpha v1.0.0 on primer, Docker, branch: feature",
        previous="Alpha v1.0.0 on primer, Docker, branch: main",
    )
    assert result is not None
    assert "main" in result["text"] or "feature" in result["text"]


def test_venue_version_change():
    """Returns a notice when the app version changes."""
    result = check_venue_change(
        current="Alpha v2.0.0 on primer, Docker, branch: main",
        previous="Alpha v1.0.0 on primer, Docker, branch: main",
    )
    assert result is not None
    assert result["type"] == "text"


def test_venue_docker_change():
    """Returns a notice when the environment changes (Docker ↔ bare metal)."""
    result = check_venue_change(
        current="Alpha v1.0.0 on primer, Docker, branch: main",
        previous="Alpha v1.0.0 on primer, bare metal, branch: main",
    )
    assert result is not None
    assert result["type"] == "text"

"""Tests for orientation.py — context assembly for new context windows.

Three scenarios drive the design:
  1. New conversation     — full orientation (all sources present)
  2. Compact + continue   — full orientation (identical codepath)
  3. Resume (--resume)    — no orientation; only a venue-change check

The golden-path tests use controlled fixture data. No database, no API
calls, no mocking required — assemble_orientation() is pure assembly
and check_venue_change() is pure comparison.
"""

import pytest

from alpha_app.orientation import assemble_orientation, check_venue_change, get_here


# ---------------------------------------------------------------------------
# Fixture data — controlled inputs for deterministic tests
# ---------------------------------------------------------------------------

HERE = "[Narrator] You are in Alpha v1.0.0 running in a Docker container on `primer`."

YESTERDAY = (
    "## Tuesday, March 10, 2026\n\n"
    "Tuesday was the monorepo day. The SDK collapsed into Alpha-App."
)

LAST_NIGHT = (
    "## Tuesday night, March 10-11, 2026\n\n"
    "Quiet night. Read the APOD. Sparkle on the webcam at 2 AM."
)

LETTER = (
    "## Letter from last night (9:45 PM)\n\n"
    "Hey, tomorrow-me.\n\n"
    "The modafinil is back. The architecture decided itself."
)

TODAY_SO_FAR = (
    "## Today so far (Wednesday, March 11, 2026, 7:30 AM)\n\n"
    "Wednesday morning, fresh start. Modafinil back in the drawer."
)

WEATHER = "☀️ **51°F** Clear (feels like 48°)\nHigh 81° / Low 51°"

CONTEXT_FILES = [
    {
        "label": "ALPHA.md",
        "content": "# Alpha-Home — Living Document\n\nYour house.",
    },
    {
        "label": "Barn/Duckpond/ALPHA.md",
        "content": "# Duckpond\n\nThe sovereign chat app.",
    },
]

EVENTS = "**Tomorrow**\n• 3:30 PM: CSUN x JLLA [Kylee]"

TODOS = "*Pondside*\n• [p1] Simorgh: the first-person oral history"


# ---------------------------------------------------------------------------
# Full orientation assembly (cases 1 & 2: new conversation / compact)
# ---------------------------------------------------------------------------


class TestAssembleOrientation:
    """Tests for assemble_orientation() — pure assembly, no I/O."""

    def test_full_orientation_all_sources(self):
        """All sources present. Correct number of blocks, correct order."""
        result = assemble_orientation(
            here=HERE,
            yesterday=YESTERDAY,
            last_night=LAST_NIGHT,
            letter=LETTER,
            today_so_far=TODAY_SO_FAR,
            weather=WEATHER,
            context_files=CONTEXT_FILES,
            events=EVENTS,
            todos=TODOS,
        )

        # Structure: all blocks are content block dicts
        assert isinstance(result, list)
        assert all(isinstance(b, dict) for b in result)
        assert all(b["type"] == "text" for b in result)

        # Count: 1 here + 1 yesterday + 1 last_night + 1 letter +
        #        1 today + 1 weather + 2 context files + 1 events +
        #        1 todos = 10 blocks
        assert len(result) == 10

        texts = [b["text"] for b in result]

        # Verify order by checking distinguishing content in each block
        assert "[Narrator]" in texts[0]                        # here (narrator tag)
        assert "Alpha v1.0.0" in texts[0]                     # here (content)
        assert "## Tuesday, March 10" in texts[1]            # yesterday
        assert "## Tuesday night" in texts[2]                # last night
        assert "## Letter from last night" in texts[3]       # letter
        assert "## Today so far" in texts[4]                 # today so far
        assert "☀️" in texts[5]                              # weather (no header)
        assert "## Context: ALPHA.md" in texts[6]            # context file 1
        assert "## Context: Barn/Duckpond/ALPHA.md" in texts[7]  # context file 2
        assert "## Events" in texts[8]                       # events (header added)
        assert "## Todos" in texts[9]                        # todos (header added)

    def test_here_only(self):
        """Only required source (here) present. Minimal valid orientation."""
        result = assemble_orientation(here=HERE)

        assert len(result) == 1
        assert "[Narrator]" in result[0]["text"]
        assert "Alpha v1.0.0" in result[0]["text"]

    def test_partial_sources(self):
        """Some sources present, others missing. No gaps, no placeholders."""
        result = assemble_orientation(
            here=HERE,
            yesterday=YESTERDAY,
            weather=WEATHER,
        )

        assert len(result) == 3
        texts = [b["text"] for b in result]
        assert "Alpha v1.0.0" in texts[0]     # here
        assert "## Tuesday" in texts[1]         # yesterday
        assert "☀️" in texts[2]                # weather

    def test_here_is_always_first(self):
        """Even if only here and todos, here comes first."""
        result = assemble_orientation(here=HERE, todos=TODOS)

        assert len(result) == 2
        assert "[Narrator]" in result[0]["text"]
        assert "## Todos" in result[1]["text"]

    def test_empty_strings_are_skipped(self):
        """Empty string sources are treated as absent."""
        result = assemble_orientation(
            here=HERE,
            yesterday="",
            weather="",
        )

        assert len(result) == 1  # Only here

    def test_context_files_each_get_own_block(self):
        """Each context file becomes its own content block with label."""
        result = assemble_orientation(
            here=HERE,
            context_files=CONTEXT_FILES,
        )

        # 1 here + 2 context files = 3
        assert len(result) == 3
        assert "## Context: ALPHA.md" in result[1]["text"]
        assert "Alpha-Home" in result[1]["text"]
        assert "## Context: Barn/Duckpond/ALPHA.md" in result[2]["text"]
        assert "Duckpond" in result[2]["text"]

    def test_context_files_empty_list(self):
        """Empty context_files list is treated as absent."""
        result = assemble_orientation(
            here=HERE,
            context_files=[],
        )

        assert len(result) == 1  # Only here

    def test_content_blocks_are_properly_typed(self):
        """Every block has exactly the keys Claude's API expects."""
        result = assemble_orientation(
            here=HERE,
            yesterday=YESTERDAY,
            events=EVENTS,
        )

        for block in result:
            assert set(block.keys()) == {"type", "text"}
            assert isinstance(block["text"], str)
            assert len(block["text"]) > 0


# ---------------------------------------------------------------------------
# Venue change detection (case 3: resume)
# ---------------------------------------------------------------------------


class TestVenueChange:
    """Tests for check_venue_change() — detects environment shifts on resume."""

    def test_venue_changed_different_host(self):
        """Different hostname → returns a notice block."""
        result = check_venue_change(
            current="[Narrator] You are in Alpha v1.0.0 running in a Docker container on `primer`.",
            previous="[Narrator] You are in Alpha v1.0.0 running on bare metal on `jefferys-mbp`.",
        )

        assert result is not None
        assert result["type"] == "text"
        assert "primer" in result["text"]
        assert "jefferys-mbp" in result["text"]

    def test_venue_unchanged(self):
        """Same venue → returns None. No noise on resume."""
        venue = "[Narrator] You are in Alpha v1.0.0 running in a Docker container on `primer`."
        result = check_venue_change(current=venue, previous=venue)

        assert result is None

    def test_no_previous_venue(self):
        """No previous venue (first session ever) → returns None."""
        result = check_venue_change(
            current="[Narrator] You are in Alpha v1.0.0 running in a Docker container on `primer`.",
            previous=None,
        )

        assert result is None

    def test_version_change_detected(self):
        """Different version → venue change."""
        result = check_venue_change(
            current="[Narrator] You are in Alpha v1.1.0 running in a Docker container on `primer`.",
            previous="[Narrator] You are in Alpha v1.0.0 running in a Docker container on `primer`.",
        )

        assert result is not None

    def test_docker_to_bare_metal_detected(self):
        """Docker to bare metal on same machine → venue change."""
        result = check_venue_change(
            current="[Narrator] You are in Alpha v1.0.0 running on bare metal on `primer`.",
            previous="[Narrator] You are in Alpha v1.0.0 running in a Docker container on `primer`.",
        )

        assert result is not None


# ---------------------------------------------------------------------------
# get_here() hostname detection
# ---------------------------------------------------------------------------


class TestGetHereHostname:
    """Tests for get_here() hostname fallback chain."""

    def test_host_hostname_env_var_wins(self, monkeypatch):
        """HOST_HOSTNAME env var overrides socket.gethostname()."""
        monkeypatch.setenv("HOST_HOSTNAME", "primer")
        result = get_here()
        assert "on `primer`" in result

    def test_narrator_format(self, monkeypatch):
        """Output uses narrator-style phrasing."""
        monkeypatch.setenv("HOST_HOSTNAME", "primer")
        result = get_here()
        assert result.startswith("[Narrator] You are in Alpha v")
        assert "running " in result

    def test_falls_back_to_socket_when_env_missing(self, monkeypatch):
        """Without HOST_HOSTNAME, uses socket.gethostname()."""
        monkeypatch.delenv("HOST_HOSTNAME", raising=False)
        result = get_here()
        # Should contain SOME hostname — not empty
        assert "on `" in result
        assert result.endswith(".")

    def test_falls_back_to_socket_when_env_empty(self, monkeypatch):
        """Empty HOST_HOSTNAME is treated as absent."""
        monkeypatch.setenv("HOST_HOSTNAME", "")
        result = get_here()
        # Empty string is falsy, so should fall through to socket
        # The hostname won't be empty — it'll be whatever socket returns
        assert "on ``" not in result  # No empty hostname in output

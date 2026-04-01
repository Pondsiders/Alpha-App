"""Tests for suggest.py — memory suggestion pipeline.

Tests the parsing, formatting, and integration logic.
Ollama calls are mocked — the LLM quality is a prompt-engineering
concern, not a unit-test concern.
"""

from __future__ import annotations

import json

import pytest

from alpha_app.suggest import _parse_memorables, format_intro_block, suggest


# ---------------------------------------------------------------------------
# Tests: _parse_memorables
# ---------------------------------------------------------------------------


class TestParseMemorables:
    def test_valid_json_with_memorables_key(self):
        raw = json.dumps({"memorables": ["first moment", "second moment"]})
        assert _parse_memorables(raw) == ["first moment", "second moment"]

    def test_empty_memorables_list(self):
        raw = json.dumps({"memorables": []})
        assert _parse_memorables(raw) == []

    def test_bare_array_fallback(self):
        raw = json.dumps(["moment one", "moment two"])
        assert _parse_memorables(raw) == ["moment one", "moment two"]

    def test_strips_whitespace(self):
        raw = json.dumps({"memorables": ["  padded  ", "\n newlined \n"]})
        assert _parse_memorables(raw) == ["padded", "newlined"]

    def test_filters_empty_strings(self):
        raw = json.dumps({"memorables": ["real", "", "  ", "also real"]})
        assert _parse_memorables(raw) == ["real", "also real"]

    def test_filters_non_strings(self):
        raw = json.dumps({"memorables": ["real", 42, None, "also real"]})
        assert _parse_memorables(raw) == ["real", "also real"]

    def test_empty_string_input(self):
        assert _parse_memorables("") == []

    def test_none_like_empty(self):
        # _parse_memorables expects str, but handle edge case
        assert _parse_memorables("") == []

    def test_invalid_json(self):
        assert _parse_memorables("not json at all") == []

    def test_json_with_wrong_structure(self):
        raw = json.dumps({"something_else": "value"})
        assert _parse_memorables(raw) == []

    def test_json_number(self):
        assert _parse_memorables("42") == []

    def test_json_string(self):
        assert _parse_memorables('"just a string"') == []

    def test_whitespace_around_json(self):
        raw = f'  {json.dumps({"memorables": ["moment"]})}  '
        assert _parse_memorables(raw) == ["moment"]


# ---------------------------------------------------------------------------
# Tests: format_intro_block
# ---------------------------------------------------------------------------


class TestFormatIntroBlock:
    def test_single_memorable(self):
        result = format_intro_block(["Jeffery said something funny"])
        assert result is not None
        assert result.startswith("[Narrator]")
        assert "- Jeffery said something funny" in result

    def test_multiple_memorables(self):
        result = format_intro_block(["first", "second", "third"])
        assert result is not None
        assert "- first\n- second\n- third" in result

    def test_empty_list_returns_none(self):
        assert format_intro_block([]) is None

    def test_block_starts_with_narrator_tag(self):
        result = format_intro_block(["moment"])
        assert result is not None
        assert result.startswith("[Narrator] Alpha, consider storing")

    def test_block_contains_instruction_line(self):
        result = format_intro_block(["moment"])
        assert result is not None
        assert "consider storing these from the previous turn:" in result

    def test_block_ends_with_no_output_instruction(self):
        """The prompt must instruct Alpha to only store, not respond with text."""
        result = format_intro_block(["moment"])
        assert result is not None
        assert "Do not produce any text output" in result
        assert "ONLY output should be store tool calls" in result

    def test_format_matches_narrator_convention(self):
        """Verify the output uses [Narrator] convention for post-turn suggest."""
        result = format_intro_block(
            ["Jeffery offered Alpha a hit of California citrus"]
        )
        assert result is not None
        assert result.startswith("[Narrator]")
        assert "Do not produce any text output" in result
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Tests: suggest (with mocked Ollama)
# ---------------------------------------------------------------------------


class TestSuggest:
    @pytest.mark.asyncio
    async def test_suggest_returns_empty_when_ollama_url_missing(self, monkeypatch):
        """When OLLAMA_URL is empty, suggest returns empty without calling anything."""
        monkeypatch.setattr("alpha_app.suggest.OLLAMA_URL", "")
        result = await suggest("hello", "hi there")
        assert result == []

    @pytest.mark.asyncio
    async def test_suggest_returns_empty_when_model_missing(self, monkeypatch):
        """When OLLAMA_CHAT_MODEL is empty, suggest returns empty."""
        monkeypatch.setattr("alpha_app.suggest.OLLAMA_CHAT_MODEL", "")
        result = await suggest("hello", "hi there")
        assert result == []

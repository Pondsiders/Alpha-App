"""Tests for suggest.py — memory suggestion pipeline.

Tests the JSON parsing (with and without markdown code fences),
the suggest prompt formatting, and the Ollama call gating.
"""

from unittest.mock import AsyncMock, patch

import pytest

from alpha_app.suggest import _parse_memorables, format_suggest_prompt, suggest


# ---------------------------------------------------------------------------
# Tests: _parse_memorables
# ---------------------------------------------------------------------------


class TestParseMemorables:
    def test_valid_json_with_memorables_key(self):
        result = _parse_memorables('{"memorables": ["one", "two"]}')
        assert result == ["one", "two"]

    def test_empty_memorables_list(self):
        result = _parse_memorables('{"memorables": []}')
        assert result == []

    def test_bare_array_fallback(self):
        result = _parse_memorables('["one", "two"]')
        assert result == ["one", "two"]

    def test_strips_whitespace(self):
        result = _parse_memorables('{"memorables": ["  padded  "]}')
        assert result == ["padded"]

    def test_filters_empty_strings(self):
        result = _parse_memorables('{"memorables": ["real", "", "  "]}')
        assert result == ["real"]

    def test_filters_non_strings(self):
        result = _parse_memorables('{"memorables": ["real", 42, null]}')
        assert result == ["real"]

    def test_empty_string_input(self):
        assert _parse_memorables("") == []

    def test_none_like_empty(self):
        assert _parse_memorables("null") == []

    def test_invalid_json(self):
        assert _parse_memorables("not json at all") == []

    def test_json_with_wrong_structure(self):
        assert _parse_memorables('{"other_key": "value"}') == []

    def test_json_number(self):
        assert _parse_memorables("42") == []

    def test_json_string(self):
        assert _parse_memorables('"just a string"') == []

    def test_whitespace_around_json(self):
        result = _parse_memorables('  {"memorables": ["trimmed"]}  ')
        assert result == ["trimmed"]

    def test_markdown_code_fences_stripped(self):
        """Qwen 3.5 4B wraps JSON in ```json ... ``` despite format: json."""
        result = _parse_memorables(
            '```json\n{"memorables": ["fenced"]}\n```'
        )
        assert result == ["fenced"]

    def test_markdown_code_fences_no_language(self):
        result = _parse_memorables('```\n{"memorables": ["bare"]}\n```')
        assert result == ["bare"]


# ---------------------------------------------------------------------------
# Tests: format_suggest_prompt
# ---------------------------------------------------------------------------


class TestFormatSuggestPrompt:
    def test_single_memorable(self):
        result = format_suggest_prompt(["Jeffery said something funny"])
        assert result is not None
        assert "- Jeffery said something funny" in result

    def test_multiple_memorables(self):
        result = format_suggest_prompt(["first", "second", "third"])
        assert result is not None
        assert "- first\n- second\n- third" in result

    def test_empty_list_returns_none(self):
        assert format_suggest_prompt([]) is None

    def test_starts_with_narrator_tag(self):
        result = format_suggest_prompt(["moment"])
        assert result is not None
        assert result.startswith("[Narrator]")

    def test_contains_store_instruction(self):
        result = format_suggest_prompt(["moment"])
        assert result is not None
        assert "cortex.store" in result

    def test_contains_stop_instruction(self):
        result = format_suggest_prompt(["moment"])
        assert result is not None
        assert "stop" in result.lower()

    def test_no_mention_of_intro(self):
        result = format_suggest_prompt(["moment"])
        assert result is not None
        assert "intro" not in result.lower()
        assert "Intro" not in result


# ---------------------------------------------------------------------------
# Tests: suggest (the Ollama call)
# ---------------------------------------------------------------------------


class TestSuggest:
    async def test_suggest_returns_empty_when_ollama_url_missing(self):
        with patch("alpha_app.suggest.OLLAMA_URL", ""):
            result = await suggest("hello", "hi there")
            assert result == []

    async def test_suggest_returns_empty_when_model_missing(self):
        with patch("alpha_app.suggest.OLLAMA_CHAT_MODEL", ""):
            result = await suggest("hello", "hi there")
            assert result == []

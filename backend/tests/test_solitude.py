"""Tests for solitude.py — Alpha's nighttime breath chain.

Three embarrassing failures:
1. load_program parses prompt_file (singular) and prompts (plural) formats,
   and the last: true flag.
2. Chain scheduling: a non-last breath schedules solitude at index + 1;
   the last breath schedules Dawn via _get_next_dawn_time.
3. _next_occurrence returns tomorrow when the target hour has already passed.

Bonus: out-of-range entry_index, no circadian chat, empty program, dawn_override.

Tier 1 — unit, fast, CI. No DB, no real Claude subprocess.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pendulum
import pytest
import yaml

import alpha_app.jobs.solitude as sol


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(chats=None):
    """Minimal app-state mock."""
    app = MagicMock()
    app.state.chats = chats if chats is not None else {}
    app.state.system_prompt = "system prompt"
    app.state.topic_registry = None
    return app


def _enrobe_result():
    """Minimal EnrobeResult stub — only .message is used by breathe."""
    r = MagicMock()
    r.message = MagicMock()
    r.events = []
    return r


def _mock_chat(response_text="alpha reply"):
    """Return (chat_mock, turn_instance_mock).

    chat.turn() is an async function that returns an async context manager
    yielding turn_instance.  Mirrors the real Chat.turn() signature exactly:

        async with await chat.turn() as t:
            await t.send(msg)
            response = await t.response()
    """
    mock_response = MagicMock()
    mock_response.text = response_text
    turn_inst = AsyncMock()
    turn_inst.response = AsyncMock(return_value=mock_response)

    async def _turn_func():
        @asynccontextmanager
        async def _cm():
            yield turn_inst

        return _cm()

    chat = MagicMock()
    chat.turn = _turn_func
    return chat, turn_inst


# ---------------------------------------------------------------------------
# Tests: load_program
# ---------------------------------------------------------------------------


class TestLoadProgram:
    """load_program reads a YAML file and returns a list of SolitudeEntry."""

    def test_singular_prompt_file_normalized_to_list(self, tmp_path):
        """prompt_file: foo.md → prompts: ["foo.md"]."""
        data = [
            {"hour": 21, "prompt_file": "first.md"},
            {"hour": 23, "prompt_file": "last.md", "last": True},
        ]
        p = tmp_path / "program.yaml"
        p.write_text(yaml.dump(data))

        with patch("alpha_app.jobs.solitude.PROGRAM_PATH", str(p)):
            entries = sol.load_program()

        assert len(entries) == 2
        assert entries[0].prompts == ["first.md"]
        assert entries[0].hour == 21
        assert entries[0].last is False
        assert entries[1].prompts == ["last.md"]
        assert entries[1].last is True

    def test_plural_prompts_kept_as_list(self, tmp_path):
        """prompts: [a.md, b.md] is preserved as-is."""
        data = [{"hour": 22, "prompts": ["part1.md", "part2.md"]}]
        p = tmp_path / "program.yaml"
        p.write_text(yaml.dump(data))

        with patch("alpha_app.jobs.solitude.PROGRAM_PATH", str(p)):
            entries = sol.load_program()

        assert entries[0].prompts == ["part1.md", "part2.md"]

    def test_last_defaults_to_false(self, tmp_path):
        """Entries without last: are marked last=False."""
        data = [{"hour": 20, "prompt_file": "x.md"}]
        p = tmp_path / "program.yaml"
        p.write_text(yaml.dump(data))

        with patch("alpha_app.jobs.solitude.PROGRAM_PATH", str(p)):
            entries = sol.load_program()

        assert entries[0].last is False


# ---------------------------------------------------------------------------
# Tests: _next_occurrence
# ---------------------------------------------------------------------------


class TestNextOccurrence:
    """_next_occurrence returns the next wall-clock time for a given hour."""

    def test_future_hour_is_today(self):
        """At 10 AM, the next occurrence of 14:00 is today."""
        fake_now = pendulum.datetime(2026, 4, 1, 10, 0, 0)
        with patch("pendulum.now", return_value=fake_now):
            result = sol._next_occurrence(14)
        assert result.day == 1
        assert result.hour == 14

    def test_past_hour_wraps_to_tomorrow(self):
        """At 10 PM, the next occurrence of 9:00 is tomorrow morning."""
        fake_now = pendulum.datetime(2026, 4, 1, 22, 0, 0)
        with patch("pendulum.now", return_value=fake_now):
            result = sol._next_occurrence(9)
        assert result.day == 2
        assert result.hour == 9

    def test_exact_same_hour_wraps_to_tomorrow(self):
        """At exactly 21:00, next occurrence of 21 is tomorrow — already past."""
        fake_now = pendulum.datetime(2026, 4, 1, 21, 0, 0)
        with patch("pendulum.now", return_value=fake_now):
            result = sol._next_occurrence(21)
        assert result.day == 2


# ---------------------------------------------------------------------------
# Tests: breathe — chain scheduling (the embarrassing ones)
# ---------------------------------------------------------------------------


class TestBreatheChainScheduling:
    """breathe() must schedule the right successor after completing its work."""

    @pytest.mark.asyncio
    async def test_non_last_entry_schedules_next_index(self):
        """A non-last breath schedules solitude at entry_index + 1."""
        program = [
            sol.SolitudeEntry(hour=21, prompts=["a.md"], last=False),
            sol.SolitudeEntry(hour=23, prompts=["b.md"], last=False),
            sol.SolitudeEntry(hour=1, prompts=["c.md"], last=True),
        ]
        chat, _ = _mock_chat()
        app = _make_app()
        enrobe_mock = AsyncMock(return_value=_enrobe_result())
        schedule_mock = AsyncMock()

        with (
            patch("alpha_app.jobs.solitude.load_program", return_value=program),
            patch("alpha_app.jobs.solitude.find_circadian_chat", return_value=chat),
            patch("alpha_app.jobs.solitude.enrobe", enrobe_mock),
            patch("alpha_app.jobs.solitude.schedule_job", schedule_mock),
            patch("pathlib.Path.read_text", return_value="prompt text"),
        ):
            await sol.breathe(app, entry_index=0)

        schedule_mock.assert_called_once()
        call = schedule_mock.call_args
        assert call.args[1] == "solitude"
        assert call.kwargs["entry_index"] == 1

    @pytest.mark.asyncio
    async def test_last_entry_schedules_dawn(self):
        """The last breath schedules dawn, not another solitude entry."""
        program = [sol.SolitudeEntry(hour=3, prompts=["final.md"], last=True)]
        chat, _ = _mock_chat()
        app = _make_app()
        enrobe_mock = AsyncMock(return_value=_enrobe_result())
        schedule_mock = AsyncMock()
        fake_dawn = pendulum.datetime(2026, 4, 2, 6, 0, 0)

        with (
            patch("alpha_app.jobs.solitude.load_program", return_value=program),
            patch("alpha_app.jobs.solitude.find_circadian_chat", return_value=chat),
            patch("alpha_app.jobs.solitude.enrobe", enrobe_mock),
            patch("alpha_app.jobs.solitude.schedule_job", schedule_mock),
            patch(
                "alpha_app.jobs.solitude._get_next_dawn_time",
                new_callable=AsyncMock,
                return_value=fake_dawn,
            ),
            patch("pathlib.Path.read_text", return_value="final prompt"),
        ):
            await sol.breathe(app, entry_index=0)

        schedule_mock.assert_called_once()
        call = schedule_mock.call_args
        assert call.args[1] == "dawn"


# ---------------------------------------------------------------------------
# Tests: breathe — edge cases
# ---------------------------------------------------------------------------


class TestBreatheEdgeCases:
    """Cases where breathe() must return None without crashing."""

    @pytest.mark.asyncio
    async def test_out_of_range_entry_index_returns_none(self):
        """entry_index beyond program length returns None gracefully."""
        program = [sol.SolitudeEntry(hour=21, prompts=["x.md"])]
        app = _make_app()

        with patch("alpha_app.jobs.solitude.load_program", return_value=program):
            result = await sol.breathe(app, entry_index=5)

        assert result is None

    @pytest.mark.asyncio
    async def test_no_circadian_chat_returns_none(self):
        """If Dawn didn't run today (no circadian chat), breathe returns None."""
        program = [sol.SolitudeEntry(hour=21, prompts=["x.md"])]
        app = _make_app()

        with (
            patch("alpha_app.jobs.solitude.load_program", return_value=program),
            patch("alpha_app.jobs.solitude.find_circadian_chat", return_value=None),
        ):
            result = await sol.breathe(app, entry_index=0)

        assert result is None


# ---------------------------------------------------------------------------
# Tests: start
# ---------------------------------------------------------------------------


class TestStart:
    """start() schedules the first breath or bails on an empty program."""

    @pytest.mark.asyncio
    async def test_empty_program_skips_night(self):
        """start() with an empty program does not call schedule_job."""
        app = _make_app()
        schedule_mock = AsyncMock()

        with (
            patch("alpha_app.jobs.solitude.load_program", return_value=[]),
            patch("alpha_app.jobs.solitude.schedule_job", schedule_mock),
        ):
            await sol.start(app)

        schedule_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_schedules_first_entry_at_index_0(self):
        """start() schedules solitude at entry_index=0."""
        program = [
            sol.SolitudeEntry(hour=21, prompts=["first.md"]),
            sol.SolitudeEntry(hour=23, prompts=["last.md"], last=True),
        ]
        app = _make_app()
        schedule_mock = AsyncMock()

        with (
            patch("alpha_app.jobs.solitude.load_program", return_value=program),
            patch("alpha_app.jobs.solitude.schedule_job", schedule_mock),
            patch("pendulum.now", return_value=pendulum.datetime(2026, 4, 1, 18, 0, 0)),
        ):
            await sol.start(app)

        schedule_mock.assert_called_once()
        call = schedule_mock.call_args
        assert call.args[1] == "solitude"
        assert call.kwargs["entry_index"] == 0


# ---------------------------------------------------------------------------
# Tests: _get_next_dawn_time
# ---------------------------------------------------------------------------


class TestGetNextDawnTime:
    """_get_next_dawn_time respects dawn_override in DB, falls back to 6 AM."""

    @pytest.mark.asyncio
    async def test_uses_dawn_override_and_clears_it(self):
        """A future dawn_override in DB is returned and then cleared."""
        future_dawn_str = "2026-04-02T05:30:00+00:00"
        future_dawn = pendulum.parse(future_dawn_str)

        get_state_mock = AsyncMock(return_value={"time": future_dawn_str})
        clear_state_mock = AsyncMock()

        with (
            patch("pendulum.now", return_value=pendulum.datetime(2026, 4, 1, 23, 0, 0)),
            patch("alpha_app.db.get_state", get_state_mock),
            patch("alpha_app.db.clear_state", clear_state_mock),
        ):
            result = await sol._get_next_dawn_time()

        assert result == future_dawn
        clear_state_mock.assert_called_once_with("dawn_override")

    @pytest.mark.asyncio
    async def test_defaults_to_6am_tomorrow_when_past_dawn(self):
        """Past midnight with no override → tomorrow at 6 AM."""
        with (
            patch("pendulum.now", return_value=pendulum.datetime(2026, 4, 1, 23, 0, 0)),
            patch("alpha_app.db.get_state", AsyncMock(return_value=None)),
        ):
            result = await sol._get_next_dawn_time()

        assert result.hour == 6
        assert result.day == 2

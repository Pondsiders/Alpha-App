"""Tests for dusk.py — the nudge-or-start-Solitude decision.

Three embarrassing failures:
1. Chat idle > 10 minutes → starts Solitude
2. Chat idle < 10 minutes → nudges AND reschedules Dusk 30 min later
3. No chat found → logs error, doesn't crash
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pendulum
import pytest

from alpha_app.chat import Chat
from alpha_app.jobs.dusk import IDLE_THRESHOLD, run


def _make_app(chat: Chat | None = None, system_prompt: str = "test-prompt") -> MagicMock:
    """Build a minimal app mock with app.state.chats and app.state.system_prompt."""
    app = MagicMock()
    app.state.chats = {"abc": chat} if chat else {}
    app.state.system_prompt = system_prompt
    return app


def _make_chat(updated_at: float) -> Chat:
    """Build a minimal Chat with a specific updated_at timestamp."""
    chat = Chat(id="abc")
    chat.updated_at = updated_at
    return chat


class TestDuskStartsSolitude:
    """Chat idle > 10 minutes → Solitude starts."""

    @pytest.mark.asyncio
    async def test_idle_over_threshold_starts_solitude(self):
        now_ts = 1_000_000.0
        chat = _make_chat(updated_at=now_ts - IDLE_THRESHOLD - 1)  # 601s idle
        app = _make_app(chat)

        with (
            patch("alpha_app.jobs.dusk.find_circadian_chat", return_value=chat),
            patch("alpha_app.jobs.dusk.schedule_job", new_callable=AsyncMock) as mock_schedule,
            patch("alpha_app.jobs.dusk.time") as mock_time,
            patch("alpha_app.jobs.solitude.start", new_callable=AsyncMock) as mock_start,
            patch("alpha_app.jobs.dusk.logfire"),
        ):
            mock_time.time.return_value = now_ts
            await run(app)

        mock_start.assert_awaited_once_with(app)
        mock_schedule.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exactly_at_threshold_starts_solitude(self):
        """Boundary: exactly 600s idle → Solitude (not < threshold)."""
        now_ts = 1_000_000.0
        chat = _make_chat(updated_at=now_ts - IDLE_THRESHOLD)  # exactly 600s idle
        app = _make_app(chat)

        with (
            patch("alpha_app.jobs.dusk.find_circadian_chat", return_value=chat),
            patch("alpha_app.jobs.dusk.schedule_job", new_callable=AsyncMock),
            patch("alpha_app.jobs.dusk.time") as mock_time,
            patch("alpha_app.jobs.solitude.start", new_callable=AsyncMock) as mock_start,
            patch("alpha_app.jobs.dusk.logfire"),
        ):
            mock_time.time.return_value = now_ts
            await run(app)

        mock_start.assert_awaited_once_with(app)

    @pytest.mark.asyncio
    async def test_solitude_start_does_not_interject(self):
        """When starting Solitude, no nudge is sent to the chat."""
        now_ts = 1_000_000.0
        chat = _make_chat(updated_at=now_ts - IDLE_THRESHOLD - 60)
        chat.interject = AsyncMock()
        app = _make_app(chat)

        with (
            patch("alpha_app.jobs.dusk.find_circadian_chat", return_value=chat),
            patch("alpha_app.jobs.dusk.schedule_job", new_callable=AsyncMock),
            patch("alpha_app.jobs.dusk.time") as mock_time,
            patch("alpha_app.jobs.solitude.start", new_callable=AsyncMock),
            patch("alpha_app.jobs.dusk.logfire"),
        ):
            mock_time.time.return_value = now_ts
            await run(app)

        chat.interject.assert_not_awaited()


class TestDuskNudges:
    """Chat idle < 10 minutes → nudge + reschedule."""

    @pytest.mark.asyncio
    async def test_idle_under_threshold_sends_nudge(self):
        now_ts = 1_000_000.0
        chat = _make_chat(updated_at=now_ts - IDLE_THRESHOLD + 1)  # 599s idle
        chat.interject = AsyncMock()
        app = _make_app(chat)

        with (
            patch("alpha_app.jobs.dusk.find_circadian_chat", return_value=chat),
            patch("alpha_app.jobs.dusk.schedule_job", new_callable=AsyncMock),
            patch("alpha_app.jobs.dusk.time") as mock_time,
            patch("alpha_app.jobs.dusk.logfire"),
        ):
            mock_time.time.return_value = now_ts
            await run(app)

        chat.interject.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_nudge_reschedules_dusk_30_min_later(self):
        """Reschedule target should be ~30 minutes after now."""
        now_ts = 1_000_000.0
        chat = _make_chat(updated_at=now_ts - 60)  # only 60s idle
        chat.interject = AsyncMock()
        app = _make_app(chat)

        with (
            patch("alpha_app.jobs.dusk.find_circadian_chat", return_value=chat),
            patch("alpha_app.jobs.dusk.schedule_job", new_callable=AsyncMock) as mock_schedule,
            patch("alpha_app.jobs.dusk.time") as mock_time,
            patch("alpha_app.jobs.dusk.logfire"),
        ):
            mock_time.time.return_value = now_ts
            # Pin pendulum.now() so the 30-min offset is deterministic
            frozen_now = pendulum.now()
            with patch("alpha_app.jobs.dusk.pendulum") as mock_pendulum:
                mock_pendulum.now.return_value = frozen_now
                await run(app)

        mock_schedule.assert_awaited_once()
        job_name, scheduled_at = mock_schedule.call_args[0][1], mock_schedule.call_args[0][2]
        assert job_name == "dusk"
        expected = frozen_now.add(minutes=30)
        assert scheduled_at == expected

    @pytest.mark.asyncio
    async def test_nudge_stores_system_prompt_before_interjecting(self):
        """chat._system_prompt must be set before interject is called."""
        now_ts = 1_000_000.0
        chat = _make_chat(updated_at=now_ts - 60)
        call_order = []

        async def mock_interject(content):
            call_order.append(("interject", chat._system_prompt))

        chat.interject = mock_interject
        app = _make_app(chat, system_prompt="the-real-prompt")

        with (
            patch("alpha_app.jobs.dusk.find_circadian_chat", return_value=chat),
            patch("alpha_app.jobs.dusk.schedule_job", new_callable=AsyncMock),
            patch("alpha_app.jobs.dusk.time") as mock_time,
            patch("alpha_app.jobs.dusk.logfire"),
        ):
            mock_time.time.return_value = now_ts
            await run(app)

        assert len(call_order) == 1
        assert call_order[0] == ("interject", "the-real-prompt")

    @pytest.mark.asyncio
    async def test_nudge_does_not_start_solitude(self):
        """Active chat → Solitude.start must not be called."""
        now_ts = 1_000_000.0
        chat = _make_chat(updated_at=now_ts - 60)
        chat.interject = AsyncMock()
        app = _make_app(chat)

        with (
            patch("alpha_app.jobs.dusk.find_circadian_chat", return_value=chat),
            patch("alpha_app.jobs.dusk.schedule_job", new_callable=AsyncMock),
            patch("alpha_app.jobs.dusk.time") as mock_time,
            patch("alpha_app.jobs.solitude.start", new_callable=AsyncMock) as mock_start,
            patch("alpha_app.jobs.dusk.logfire"),
        ):
            mock_time.time.return_value = now_ts
            await run(app)

        mock_start.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_just_under_threshold_nudges(self):
        """Boundary: 599s idle → nudge, NOT Solitude."""
        now_ts = 1_000_000.0
        chat = _make_chat(updated_at=now_ts - (IDLE_THRESHOLD - 1))
        chat.interject = AsyncMock()
        app = _make_app(chat)

        with (
            patch("alpha_app.jobs.dusk.find_circadian_chat", return_value=chat),
            patch("alpha_app.jobs.dusk.schedule_job", new_callable=AsyncMock),
            patch("alpha_app.jobs.dusk.time") as mock_time,
            patch("alpha_app.jobs.solitude.start", new_callable=AsyncMock) as mock_start,
            patch("alpha_app.jobs.dusk.logfire"),
        ):
            mock_time.time.return_value = now_ts
            await run(app)

        chat.interject.assert_awaited_once()
        mock_start.assert_not_awaited()


class TestDuskNoChatFound:
    """No chat → log error, don't crash."""

    @pytest.mark.asyncio
    async def test_no_chat_returns_without_error(self):
        app = _make_app(chat=None)

        with (
            patch("alpha_app.jobs.dusk.find_circadian_chat", return_value=None),
            patch("alpha_app.jobs.dusk.schedule_job", new_callable=AsyncMock) as mock_schedule,
            patch("alpha_app.jobs.dusk.logfire") as mock_logfire,
        ):
            # Should not raise
            await run(app)

        mock_schedule.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_chat_logs_error(self):
        app = _make_app(chat=None)

        with (
            patch("alpha_app.jobs.dusk.find_circadian_chat", return_value=None),
            patch("alpha_app.jobs.dusk.schedule_job", new_callable=AsyncMock),
            patch("alpha_app.jobs.dusk.logfire") as mock_logfire,
        ):
            await run(app)

        mock_logfire.error.assert_called_once()
        error_msg = mock_logfire.error.call_args[0][0]
        assert "no chat" in error_msg.lower() or "dawn" in error_msg.lower()

    @pytest.mark.asyncio
    async def test_no_chat_does_not_start_solitude(self):
        app = _make_app(chat=None)

        with (
            patch("alpha_app.jobs.dusk.find_circadian_chat", return_value=None),
            patch("alpha_app.jobs.dusk.schedule_job", new_callable=AsyncMock),
            patch("alpha_app.jobs.solitude.start", new_callable=AsyncMock) as mock_start,
            patch("alpha_app.jobs.dusk.logfire"),
        ):
            await run(app)

        mock_start.assert_not_awaited()

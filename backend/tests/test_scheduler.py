"""Tests for scheduler.py — the heartbeat.

Three embarrassing failures:
1. schedule_job writes to Postgres AND registers with APScheduler — order matters
2. sync_from_db loads future jobs, deletes overdue ones (overdue = chain death)
3. _job_wrapper deletes the DB row BEFORE running the handler

Tier 1: unit tests — mock the asyncpg pool and APScheduler.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pendulum
import pytest


# ---------------------------------------------------------------------------
# Stubs and helpers
# ---------------------------------------------------------------------------


class AppStub:
    """Minimal app stand-in — just needs app.state.scheduler."""

    class _State:
        pass

    def __init__(self):
        self.state = self._State()
        self.state.scheduler = MagicMock()


def _make_pool_mock():
    """Build a mock asyncpg pool with async execute and fetch."""
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    return pool


def _job_row(job_type: str, fire_at: pendulum.DateTime, job_id: str = None, kwargs: str = "{}"):
    """Build a fake asyncpg-style row dict for a job."""
    if job_id is None:
        job_id = f"{job_type}-{fire_at.format('YYYY-MM-DD-HHmm')}"
    return {
        "id": job_id,
        "job_type": job_type,
        "fire_at": fire_at,
        "kwargs": kwargs,
    }


# ---------------------------------------------------------------------------
# schedule_job
# ---------------------------------------------------------------------------


class TestScheduleJob:
    """schedule_job writes to Postgres AND registers with APScheduler."""

    async def test_postgres_written_before_apscheduler(self):
        """Postgres INSERT must happen before APScheduler.add_job.

        If APScheduler registration fails, the DB row is the fallback.
        If the order is reversed and the process crashes, the chain dies.
        """
        from alpha_app.scheduler import schedule_job

        app = AppStub()
        pool = _make_pool_mock()
        fire_at = pendulum.now("UTC").add(hours=1)

        call_order = []
        pool.execute = AsyncMock(side_effect=lambda *a, **kw: call_order.append("postgres") or None)
        app.state.scheduler.add_job = MagicMock(
            side_effect=lambda *a, **kw: call_order.append("apscheduler")
        )

        with patch("alpha_app.scheduler.get_pool", return_value=pool):
            await schedule_job(app, "dawn", fire_at)

        assert call_order == ["postgres", "apscheduler"], (
            "Postgres must be written before APScheduler registers the job"
        )

    async def test_job_id_format(self):
        """Job ID is formatted as {type}-{YYYY-MM-DD-HHmm}."""
        from alpha_app.scheduler import schedule_job

        app = AppStub()
        pool = _make_pool_mock()
        fire_at = pendulum.datetime(2026, 4, 3, 6, 0, tz="UTC")

        with patch("alpha_app.scheduler.get_pool", return_value=pool):
            job_id = await schedule_job(app, "dawn", fire_at)

        assert job_id == "dawn-2026-04-03-0600"

    async def test_both_halves_called(self):
        """If either half is skipped, the chain dies silently."""
        from alpha_app.scheduler import schedule_job

        app = AppStub()
        pool = _make_pool_mock()
        fire_at = pendulum.now("UTC").add(hours=2)

        with patch("alpha_app.scheduler.get_pool", return_value=pool):
            await schedule_job(app, "solitude", fire_at)

        pool.execute.assert_awaited_once()
        app.state.scheduler.add_job.assert_called_once()

    async def test_kwargs_serialized_to_json(self):
        """kwargs are JSON-serialized for storage in Postgres."""
        from alpha_app.scheduler import schedule_job

        app = AppStub()
        pool = _make_pool_mock()
        fire_at = pendulum.now("UTC").add(hours=1)

        with patch("alpha_app.scheduler.get_pool", return_value=pool):
            await schedule_job(app, "alarm", fire_at, alarm_id="abc123")

        # pool.execute(sql, job_id, job_type, fire_at, kwargs_json)
        call_args = pool.execute.call_args[0]
        kwargs_arg = call_args[4]
        assert json.loads(kwargs_arg) == {"alarm_id": "abc123"}

    async def test_job_id_passed_to_apscheduler(self):
        """APScheduler must receive the same job_id used for Postgres."""
        from alpha_app.scheduler import schedule_job

        app = AppStub()
        pool = _make_pool_mock()
        fire_at = pendulum.datetime(2026, 4, 3, 8, 30, tz="UTC")

        with patch("alpha_app.scheduler.get_pool", return_value=pool):
            job_id = await schedule_job(app, "dusk", fire_at)

        add_job_kwargs = app.state.scheduler.add_job.call_args[1]
        assert add_job_kwargs["id"] == job_id == "dusk-2026-04-03-0830"


# ---------------------------------------------------------------------------
# sync_from_db
# ---------------------------------------------------------------------------


class TestSyncFromDb:
    """sync_from_db loads future jobs and deletes overdue ones on startup."""

    async def test_future_jobs_registered_with_apscheduler(self):
        """Jobs with fire_at > now are registered with APScheduler."""
        from alpha_app.scheduler import sync_from_db

        app = AppStub()
        pool = _make_pool_mock()

        now = pendulum.datetime(2026, 4, 2, 12, 0, tz="UTC")
        future = now.add(hours=2)
        pool.fetch = AsyncMock(return_value=[_job_row("solitude", future)])

        with (
            patch("alpha_app.scheduler.get_pool", return_value=pool),
            patch("pendulum.now", return_value=now),
        ):
            loaded = await sync_from_db(app)

        assert loaded == 1
        app.state.scheduler.add_job.assert_called_once()

    async def test_overdue_jobs_deleted_not_scheduled(self):
        """Jobs with fire_at <= now are deleted from Postgres, not added to scheduler."""
        from alpha_app.scheduler import sync_from_db

        app = AppStub()
        pool = _make_pool_mock()

        now = pendulum.datetime(2026, 4, 2, 12, 0, tz="UTC")
        overdue = now.subtract(hours=1)
        pool.fetch = AsyncMock(return_value=[_job_row("dawn", overdue, job_id="dawn-2026-04-02-1100")])

        with (
            patch("alpha_app.scheduler.get_pool", return_value=pool),
            patch("pendulum.now", return_value=now),
        ):
            loaded = await sync_from_db(app)

        assert loaded == 0
        pool.execute.assert_awaited_once()
        delete_sql = pool.execute.call_args[0][0]
        assert "DELETE" in delete_sql.upper()
        app.state.scheduler.add_job.assert_not_called()

    async def test_exact_now_is_overdue(self):
        """A job with fire_at == now is overdue (fire_at <= now means chain death)."""
        from alpha_app.scheduler import sync_from_db

        app = AppStub()
        pool = _make_pool_mock()

        now = pendulum.datetime(2026, 4, 2, 6, 0, tz="UTC")
        pool.fetch = AsyncMock(return_value=[_job_row("dawn", now, job_id="dawn-2026-04-02-0600")])

        with (
            patch("alpha_app.scheduler.get_pool", return_value=pool),
            patch("pendulum.now", return_value=now),
        ):
            loaded = await sync_from_db(app)

        assert loaded == 0
        app.state.scheduler.add_job.assert_not_called()

    async def test_mixed_future_and_overdue(self):
        """Future jobs are scheduled; overdue jobs are deleted. Returns correct count."""
        from alpha_app.scheduler import sync_from_db

        app = AppStub()
        pool = _make_pool_mock()

        now = pendulum.datetime(2026, 4, 2, 12, 0, tz="UTC")
        rows = [
            _job_row("dawn", now.subtract(hours=2), job_id="dawn-2026-04-02-1000"),  # overdue
            _job_row("solitude", now.add(hours=1)),   # future
            _job_row("dusk", now.add(hours=3)),        # future
        ]
        pool.fetch = AsyncMock(return_value=rows)

        with (
            patch("alpha_app.scheduler.get_pool", return_value=pool),
            patch("pendulum.now", return_value=now),
        ):
            loaded = await sync_from_db(app)

        assert loaded == 2
        assert pool.execute.await_count == 1  # one DELETE for the overdue job
        assert app.state.scheduler.add_job.call_count == 2

    async def test_empty_db_returns_zero(self):
        """With no jobs in Postgres, loaded count is 0 and scheduler is untouched."""
        from alpha_app.scheduler import sync_from_db

        app = AppStub()
        pool = _make_pool_mock()
        pool.fetch = AsyncMock(return_value=[])

        with (
            patch("alpha_app.scheduler.get_pool", return_value=pool),
        ):
            loaded = await sync_from_db(app)

        assert loaded == 0
        app.state.scheduler.add_job.assert_not_called()
        pool.execute.assert_not_awaited()

    async def test_future_job_id_passed_to_apscheduler(self):
        """The job ID from Postgres is preserved when registering with APScheduler."""
        from alpha_app.scheduler import sync_from_db

        app = AppStub()
        pool = _make_pool_mock()

        now = pendulum.datetime(2026, 4, 2, 12, 0, tz="UTC")
        future = now.add(hours=4)
        pool.fetch = AsyncMock(return_value=[_job_row("alarm", future, job_id="alarm-2026-04-02-1600")])

        with (
            patch("alpha_app.scheduler.get_pool", return_value=pool),
            patch("pendulum.now", return_value=now),
        ):
            await sync_from_db(app)

        add_job_kwargs = app.state.scheduler.add_job.call_args[1]
        assert add_job_kwargs["id"] == "alarm-2026-04-02-1600"


# ---------------------------------------------------------------------------
# _job_wrapper
# ---------------------------------------------------------------------------


class TestJobWrapper:
    """_job_wrapper deletes the DB row before running the handler."""

    async def test_db_deleted_before_handler(self):
        """DB row must be deleted before handler runs — prevents duplicate fires on restart."""
        from alpha_app.scheduler import _job_wrapper

        app = AppStub()
        pool = _make_pool_mock()

        call_order = []
        pool.execute = AsyncMock(side_effect=lambda *a, **kw: call_order.append("delete") or None)

        async def fake_handler(app, **kwargs):
            call_order.append("handler")

        with (
            patch("alpha_app.scheduler.get_pool", return_value=pool),
            patch("alpha_app.scheduler.importlib.import_module") as mock_import,
        ):
            mock_module = MagicMock()
            mock_module.run = fake_handler
            mock_import.return_value = mock_module

            await _job_wrapper(app, "dawn-2026-04-03-0600", "dawn")

        assert call_order == ["delete", "handler"], (
            "DB row must be deleted BEFORE handler runs; "
            "crash after delete = intentional chain break, not duplicate fire"
        )

    async def test_handler_crash_does_not_re_delete(self):
        """Handler crash after DB delete lets the chain break cleanly — no retry, no duplicate."""
        from alpha_app.scheduler import _job_wrapper

        app = AppStub()
        pool = _make_pool_mock()

        async def crashing_handler(app, **kwargs):
            raise RuntimeError("handler exploded")

        with (
            patch("alpha_app.scheduler.get_pool", return_value=pool),
            patch("alpha_app.scheduler.importlib.import_module") as mock_import,
        ):
            mock_module = MagicMock()
            mock_module.run = crashing_handler
            mock_import.return_value = mock_module

            with pytest.raises(RuntimeError, match="handler exploded"):
                await _job_wrapper(app, "dawn-2026-04-03-0600", "dawn")

        # DELETE was called exactly once — before the crash, not retried after
        pool.execute.assert_awaited_once()

    async def test_correct_handler_resolved_from_registry(self):
        """_job_wrapper imports and calls the handler from JOB_HANDLERS, not a hardcoded path."""
        from alpha_app.scheduler import _job_wrapper, JOB_HANDLERS

        app = AppStub()
        pool = _make_pool_mock()
        mock_handler = AsyncMock()

        with (
            patch("alpha_app.scheduler.get_pool", return_value=pool),
            patch("alpha_app.scheduler.importlib.import_module") as mock_import,
        ):
            module_path, func_name = JOB_HANDLERS["dawn"].rsplit(":", 1)
            mock_module = MagicMock()
            setattr(mock_module, func_name, mock_handler)
            mock_import.return_value = mock_module

            await _job_wrapper(app, "dawn-2026-04-03-0600", "dawn")

        mock_import.assert_called_once_with(module_path)
        mock_handler.assert_awaited_once_with(app)

    async def test_kwargs_forwarded_to_handler(self):
        """kwargs passed to _job_wrapper are forwarded to the handler."""
        from alpha_app.scheduler import _job_wrapper

        app = AppStub()
        pool = _make_pool_mock()
        mock_handler = AsyncMock()

        with (
            patch("alpha_app.scheduler.get_pool", return_value=pool),
            patch("alpha_app.scheduler.importlib.import_module") as mock_import,
        ):
            mock_module = MagicMock()
            mock_module.run = mock_handler
            mock_import.return_value = mock_module

            await _job_wrapper(app, "alarm-2026-04-03-0700", "alarm", alarm_id="xyz")

        mock_handler.assert_awaited_once_with(app, alarm_id="xyz")

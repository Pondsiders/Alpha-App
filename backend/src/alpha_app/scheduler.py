"""scheduler.py — The heartbeat.

APScheduler as a pure in-memory executor. No SQLAlchemyJobStore, no pickle.
Job persistence lives in app.jobs (our own Postgres table, plain JSON).

On startup: read app.jobs, populate APScheduler. Dead reckoning.
On schedule: write to Postgres first, then register with APScheduler.
On fire: delete the DB row, run the handler. If it fails, chain breaks.
"""

import importlib
import json

import logfire
import pendulum
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from alpha_app.db import get_pool

# The only job types that exist. String -> import path.
JOB_HANDLERS = {
    "dawn": "alpha_app.jobs.dawn:run",
    "dusk": "alpha_app.jobs.dusk:run",
    "solitude": "alpha_app.jobs.solitude:breathe",
    "alarm": "alpha_app.jobs.alarm:run",
}


def create_scheduler(app) -> AsyncIOScheduler:
    """Create a pure in-memory scheduler. No job store."""
    scheduler = AsyncIOScheduler()  # timezone from TZ env var
    app.state.scheduler = scheduler
    return scheduler


async def sync_from_db(app) -> int:
    """Startup: read all jobs from Postgres, populate APScheduler.

    Overdue jobs are deleted (chain death — intentional).
    Returns the number of jobs loaded.
    """
    pool = get_pool()
    scheduler = app.state.scheduler
    now = pendulum.now()
    loaded = 0

    rows = await pool.fetch("SELECT id, job_type, fire_at, kwargs FROM app.jobs ORDER BY fire_at")
    for row in rows:
        fire_at = pendulum.instance(row["fire_at"])
        if fire_at <= now:
            await pool.execute("DELETE FROM app.jobs WHERE id = $1", row["id"])
            logfire.warn("sync: deleted overdue job {id}", id=row["id"])
            continue

        kwargs = json.loads(row["kwargs"]) if row["kwargs"] else {}
        scheduler.add_job(
            _job_wrapper,
            DateTrigger(run_date=fire_at),
            args=[app, row["id"], row["job_type"]],
            kwargs=kwargs,
            id=row["id"],
            replace_existing=True,
        )
        loaded += 1

    logfire.info("sync: loaded {count} jobs from Postgres", count=loaded)
    return loaded


async def schedule_job(app, job_type: str, fire_at: pendulum.DateTime, **kwargs) -> str:
    """Schedule a job: write to Postgres, then register with APScheduler.

    Returns the job ID.
    """
    pool = get_pool()
    job_id = f"{job_type}-{fire_at.format('YYYY-MM-DD-HHmm')}"

    await pool.execute("""
        INSERT INTO app.jobs (id, job_type, fire_at, kwargs)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (id) DO UPDATE
            SET fire_at = EXCLUDED.fire_at, kwargs = EXCLUDED.kwargs
    """, job_id, job_type, fire_at, json.dumps(kwargs) if kwargs else "{}")

    scheduler = app.state.scheduler
    scheduler.add_job(
        _job_wrapper,
        DateTrigger(run_date=fire_at),
        args=[app, job_id, job_type],
        kwargs=kwargs,
        id=job_id,
        replace_existing=True,
    )

    logfire.info("scheduled: {id} at {time}", id=job_id, time=fire_at)
    return job_id


async def remove_job(app, job_id: str) -> None:
    """Remove a job from both Postgres and APScheduler."""
    pool = get_pool()
    await pool.execute("DELETE FROM app.jobs WHERE id = $1", job_id)
    try:
        app.state.scheduler.remove_job(job_id)
    except Exception:
        pass


async def remove_all_jobs(app) -> None:
    """Nuclear swap — clear everything."""
    pool = get_pool()
    await pool.execute("DELETE FROM app.jobs")
    app.state.scheduler.remove_all_jobs()


async def list_jobs(app) -> list[dict]:
    """List all pending jobs from Postgres (the source of truth)."""
    pool = get_pool()
    rows = await pool.fetch("SELECT id, job_type, fire_at, kwargs FROM app.jobs ORDER BY fire_at")
    return [
        {
            "id": row["id"],
            "job_type": row["job_type"],
            "fire_at": str(row["fire_at"]),
            "kwargs": json.loads(row["kwargs"]) if row["kwargs"] else {},
        }
        for row in rows
    ]


async def _job_wrapper(app, job_id: str, job_type: str, **kwargs) -> None:
    """Wraps every job: delete DB row, resolve handler, run it.

    If the handler fails, the chain breaks — no successor gets scheduled.
    That's the feature.
    """
    pool = get_pool()
    await pool.execute("DELETE FROM app.jobs WHERE id = $1", job_id)

    module_path, func_name = JOB_HANDLERS[job_type].rsplit(":", 1)
    module = importlib.import_module(module_path)
    handler = getattr(module, func_name)

    with logfire.span("alpha.job.{job_type}", job_type=job_type, job_id=job_id):
        await handler(app, **kwargs)

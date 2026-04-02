"""schedule_api.py — HTTP API for the circadian chain + alarms.

Two separate concerns:

    /api/schedule/next   — the circadian linked-list pointer (one slot)
    /api/schedule/alarms — one-shot alarms (a list, separate concern)

The circadian chain self-perpetuates: each event schedules the next.
This API lets you inspect, repair, or break the chain.
Alarms are independent fire-and-forget messages.
"""

from typing import Literal

import pendulum
from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/schedule", tags=["schedule"])


def _parse_local(time_str: str) -> pendulum.DateTime:
    """Parse a time string as local time (from TZ env var).

    If the string already has timezone info (e.g. +05:30), use it as-is.
    If naive (no tz), interpret as Pondside local time.
    """
    dt = pendulum.parse(time_str)
    if dt.timezone_name == "UTC" and "+" not in time_str and "Z" not in time_str:
        dt = pendulum.parse(time_str, tz=pendulum.now().timezone)
    return dt


# -- Models ------------------------------------------------------------------


class NextEventRequest(BaseModel):
    """The next circadian event. One slot — atomic swap."""
    type: Literal["dawn", "solitude", "dusk"]
    time: str  # ISO 8601 datetime, local time
    entry_index: int | None = None  # solitude only: which program entry


class AlarmRequest(BaseModel):
    """A one-shot alarm. Fire and forget."""
    time: str
    message: str


# -- Circadian pointer: /api/schedule/next -----------------------------------


@router.get("/next")
async def get_next(request: Request):
    """What's the next circadian event? Returns the one slot, or null."""
    from alpha_app.scheduler import list_jobs
    jobs = await list_jobs(request.app)
    # Filter to circadian jobs only (not alarms)
    circadian = [j for j in jobs if j["job_type"] in ("dawn", "solitude", "dusk")]
    if not circadian:
        return None
    # Return the soonest one
    return min(circadian, key=lambda j: j["fire_at"])


@router.post("/next")
async def set_next(request: Request, body: NextEventRequest):
    """Set the next circadian event. Atomic swap: clears any existing
    circadian job, then schedules the new one."""
    from alpha_app.scheduler import schedule_job, remove_all_jobs
    from alpha_app.db import get_pool

    dt = _parse_local(body.time)

    # Clear existing circadian jobs (not alarms)
    pool = get_pool()
    await pool.execute(
        "DELETE FROM app.jobs WHERE job_type IN ('dawn', 'solitude', 'dusk')"
    )

    # Also remove from APScheduler in-memory
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler:
        for job in scheduler.get_jobs():
            if any(t in job.id for t in ("dawn", "solitude", "dusk")):
                job.remove()

    # Schedule the new one
    kwargs = {}
    if body.entry_index is not None:
        kwargs["entry_index"] = body.entry_index

    job_id = await schedule_job(request.app, body.type, dt, **kwargs)
    return {"type": body.type, "time": str(dt), "job_id": job_id}


@router.delete("/next")
async def clear_next(request: Request):
    """Break the chain. Silence."""
    from alpha_app.db import get_pool

    pool = get_pool()
    await pool.execute(
        "DELETE FROM app.jobs WHERE job_type IN ('dawn', 'solitude', 'dusk')"
    )

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler:
        for job in scheduler.get_jobs():
            if any(t in job.id for t in ("dawn", "solitude", "dusk")):
                job.remove()

    return {"cleared": True}


# -- Alarms: /api/schedule/alarms -------------------------------------------


@router.get("/alarms")
async def get_alarms(request: Request):
    """List all pending alarms."""
    from alpha_app.scheduler import list_jobs
    jobs = await list_jobs(request.app)
    return [j for j in jobs if j["job_type"] == "alarm"]


@router.post("/alarms")
async def set_alarms(request: Request, alarms: list[AlarmRequest]):
    """Replace all alarms. Nuclear swap on the alarm list only."""
    from alpha_app.scheduler import schedule_job
    from alpha_app.db import get_pool

    # Clear existing alarms
    pool = get_pool()
    await pool.execute("DELETE FROM app.jobs WHERE job_type = 'alarm'")

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler:
        for job in scheduler.get_jobs():
            if "alarm" in job.id:
                job.remove()

    # Schedule new ones
    results = []
    for alarm in alarms:
        dt = _parse_local(alarm.time)
        job_id = await schedule_job(request.app, "alarm", dt, message=alarm.message)
        results.append({"time": str(dt), "message": alarm.message, "job_id": job_id})

    return results


# -- Legacy: GET and DELETE on /api/schedule (backward compat) ---------------


@router.get("")
async def get_all(request: Request):
    """List ALL pending jobs (circadian + alarms). Legacy endpoint."""
    from alpha_app.scheduler import list_jobs
    return await list_jobs(request.app)


@router.delete("")
async def delete_all(request: Request):
    """Nuclear swap — clear everything. Circadian + alarms."""
    from alpha_app.scheduler import remove_all_jobs
    await remove_all_jobs(request.app)
    return {"cleared": True}

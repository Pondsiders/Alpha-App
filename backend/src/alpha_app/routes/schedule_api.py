"""schedule_api.py — HTTP API for schedule inspection and control.

Alpha manipulates her own schedule via curl from Bash, documented
by the schedule skill. No MCP tool needed — the API is the interface.

Endpoints:
    GET  /api/schedule          — list pending jobs
    POST /api/schedule/dawn     — schedule a Dawn
    POST /api/schedule/override — set dawn override (travel)
    POST /api/schedule/alarm    — set a custom alarm
    DELETE /api/schedule        — clear all jobs (nuclear swap)
"""

from datetime import datetime

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
        # Naive input — pendulum defaulted to UTC. Re-parse as local.
        dt = pendulum.parse(time_str, tz=pendulum.now().timezone)
    return dt


class DawnRequest(BaseModel):
    time: str  # ISO 8601 datetime, e.g. "2026-03-31T06:00:00"


class OverrideRequest(BaseModel):
    dawn_time: str  # ISO 8601 datetime with timezone


class SolitudeRequest(BaseModel):
    time: str          # ISO 8601 datetime, e.g. "2026-04-01T22:00:00"
    entry_index: int = 0  # which program entry to start from


class AlarmRequest(BaseModel):
    time: str      # ISO 8601 datetime
    message: str   # prompt text to inject


@router.get("")
async def get_jobs(request: Request):
    """List all pending scheduled jobs."""
    from alpha_app.scheduler import list_jobs
    return await list_jobs(request.app)


@router.post("/dawn")
async def post_dawn(request: Request, body: DawnRequest):
    """Schedule a Dawn at a specific time. The bootstrap."""
    from alpha_app.scheduler import schedule_job
    dt = _parse_local(body.time)
    job_id = await schedule_job(request.app, "dawn", dt)
    return {"scheduled": "dawn", "time": str(dt), "job_id": job_id}


@router.post("/override")
async def set_override(request: Request, body: OverrideRequest):
    """Set a dawn override for travel or schedule changes."""
    from alpha_app.db import set_state
    dt = _parse_local(body.dawn_time)
    await set_state("dawn_override", {"time": str(dt)})
    return {"override_set": str(dt)}


@router.post("/solitude")
async def post_solitude(request: Request, body: SolitudeRequest):
    """Schedule a Solitude breath. For bootstrapping the night chain."""
    from alpha_app.scheduler import schedule_job
    dt = _parse_local(body.time)
    job_id = await schedule_job(request.app, "solitude", dt, entry_index=body.entry_index)
    return {"scheduled": "solitude", "time": str(dt), "entry_index": body.entry_index, "job_id": job_id}


@router.post("/alarm")
async def post_alarm(request: Request, body: AlarmRequest):
    """Set a custom alarm — drops a message into today's chat."""
    from alpha_app.scheduler import schedule_job
    dt = _parse_local(body.time)
    job_id = await schedule_job(request.app, "alarm", dt, message=body.message)
    return {"alarm_set": str(dt), "message": body.message, "job_id": job_id}


@router.delete("")
async def delete_all(request: Request):
    """Nuclear swap — clear all scheduled jobs."""
    from alpha_app.scheduler import remove_all_jobs
    await remove_all_jobs(request.app)
    return {"cleared": True}

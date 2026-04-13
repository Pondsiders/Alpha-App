"""schedule_api.py — HTTP API for the circadian chain, Solitude program, and alarms.

Three concerns:

    /api/schedule/...    — the circadian chain (Dawn↔Dusk)
    /api/solitude/...    — the Solitude program (editable at runtime)
    /api/schedule/alarms — one-shot alarms (fire and forget)

The circadian chain self-perpetuates: each event schedules the next.
Solitude is a fire-and-forget task launched by Dusk; the program is
editable at runtime via these endpoints. controlpanel is a thin CLI
that calls these endpoints.
"""

import asyncio
from typing import Literal

import pendulum
from fastapi import APIRouter, Request
from pydantic import BaseModel

from alpha_app.clock import PSOResponse

router = APIRouter(prefix="/api/schedule", tags=["schedule"], default_response_class=PSOResponse)
solitude_router = APIRouter(prefix="/api/solitude", tags=["solitude"], default_response_class=PSOResponse)


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
    circadian = [j for j in jobs if j["job_type"] in ("dawn", "solitude", "dusk")]
    if not circadian:
        return PSOResponse(content=None)
    return PSOResponse(content=min(circadian, key=lambda j: j["fire_at"]))


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
    return PSOResponse(content=await list_jobs(request.app))


@router.delete("")
async def delete_all(request: Request):
    """Nuclear swap — clear everything. Circadian + alarms."""
    from alpha_app.scheduler import remove_all_jobs
    await remove_all_jobs(request.app)
    return {"cleared": True}


# -- Solitude program: /api/solitude ------------------------------------------


class SolitudeEntryRequest(BaseModel):
    """A Solitude program entry."""
    fire_at: str  # HH:MM (24-hour)
    prompt: str
    recurring: bool = True


class SolitudeStopRequest(BaseModel):
    """Stop Solitude for the night."""
    reason: str = ""


@solitude_router.get("")
async def get_program(request: Request):
    """Show the Solitude program."""
    from alpha_app.db import get_pool
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT id, fire_at, prompt, recurring, created_at"
        " FROM app.solitude_program ORDER BY (fire_at + interval '6 hours')"
    )
    return PSOResponse(content=[
        {
            "id": row["id"],
            "fire_at": row["fire_at"],
            "prompt": row["prompt"],
            "recurring": row["recurring"],
            "created_at": row["created_at"],
        }
        for row in rows
    ])


@solitude_router.post("")
async def set_entry(request: Request, body: SolitudeEntryRequest):
    """Add or update a Solitude program entry."""
    from alpha_app.db import get_pool
    import datetime

    pool = get_pool()
    parts = body.fire_at.split(":")
    fire_time = datetime.time(int(parts[0]), int(parts[1]))

    row = await pool.fetchrow("""
        INSERT INTO app.solitude_program (fire_at, prompt, recurring)
        VALUES ($1, $2, $3)
        RETURNING id
    """, fire_time, body.prompt, body.recurring)

    # Signal the running Solitude task to reload
    changed = getattr(request.app.state, "solitude_changed", None)
    if changed:
        changed.set()

    return {"id": row["id"], "fire_at": body.fire_at, "prompt": body.prompt}


@solitude_router.delete("/{entry_id}")
async def delete_entry(request: Request, entry_id: int):
    """Delete a Solitude program entry."""
    from alpha_app.db import get_pool
    pool = get_pool()
    await pool.execute("DELETE FROM app.solitude_program WHERE id = $1", entry_id)

    changed = getattr(request.app.state, "solitude_changed", None)
    if changed:
        changed.set()

    return {"deleted": entry_id}


@solitude_router.post("/stop")
async def stop_solitude(request: Request, body: SolitudeStopRequest | None = None):
    """Stop Solitude for the night. Shuts down the scheduler directly."""
    import logfire

    reason = body.reason if body else ""
    logfire.warn("solitude: stopped via API: {reason}", reason=reason or "(no reason)")

    # Shut down the scheduler — this causes the Solitude task to exit
    scheduler = getattr(request.app.state, "solitude_scheduler", None)
    if scheduler:
        scheduler.shutdown(wait=False)

    return {"stopped": True, "reason": reason}


# -- Context cards: /api/context ----------------------------------------------

context_router = APIRouter(prefix="/api/context", tags=["context"])


class ContextCardRequest(BaseModel):
    """A context card — living knowledge."""
    text: str


@context_router.get("")
async def get_context(request: Request):
    """Show recent context cards."""
    from alpha_app.memories.db import get_pool as get_cortex_pool
    pool = await get_cortex_pool()
    rows = await pool.fetch(
        "SELECT id, text, tokens, created_at"
        " FROM cortex.context ORDER BY created_at DESC LIMIT 100"
    )
    return PSOResponse(content=[
        {
            "id": row["id"],
            "text": row["text"],
            "tokens": row["tokens"],
            "created_at": row["created_at"],
        }
        for row in rows
    ])


@context_router.post("")
async def add_context(request: Request, body: ContextCardRequest):
    """Add a context card."""
    from alpha_app.clock import count_tokens
    from alpha_app.memories.db import get_pool as get_cortex_pool, embed

    tokens = count_tokens(body.text)
    embedding = await embed(body.text)

    pool = await get_cortex_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO cortex.context (text, tokens, embedding)"
            " VALUES ($1, $2, $3) RETURNING id",
            body.text, tokens, embedding,
        )

    return {"id": row["id"], "tokens": tokens}

"""solitude.py — Alpha's nighttime existence.

A fire-and-forget async task launched by Dusk. Owns its own local
APScheduler instance (cheap to create, cheap to destroy). Reads the
program from app.solitude_program in Postgres. Each breath is its
own Logfire span.

When the program changes at runtime, the old scheduler is destroyed
and a new one is created from scratch. No diffing, no clearing jobs,
no stale state. Clean slate every time.

Not part of the chain. The chain is Dawn↔Dusk. If this crashes,
Dawn still fires.
"""

import asyncio

import logfire
import pendulum
from apscheduler.events import EVENT_JOB_EXECUTED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from alpha_app.db import get_pool
from alpha_app.routes.enrobe import enrobe


async def run(app) -> None:
    """Solitude's entry point. Launched by Dusk as asyncio.create_task().

    Wraps the inner loop in exception handling so crashes log cleanly.
    """
    try:
        await _solitude_loop(app)
    except Exception as e:
        logfire.error("solitude: crashed: {err}", err=str(e))


async def _solitude_loop(app) -> None:
    """The loop. Create scheduler, run program, react to changes."""
    chat = getattr(app.state, "solitude_chat", None)
    if not chat:
        logfire.warn("solitude: no chat available, exiting")
        return

    # The event that signals "program changed, rebuild scheduler"
    if not hasattr(app.state, "solitude_changed"):
        app.state.solitude_changed = asyncio.Event()

    # The event that signals "abort this night — a breath hit a
    # synthetic refusal from the Claude Code harness, and every
    # subsequent breath on the same resumed session will hit the same
    # thing. Scream and die cleanly."
    if not hasattr(app.state, "solitude_abort"):
        app.state.solitude_abort = asyncio.Event()

    while True:
        program = await _load_program()
        if not program:
            logfire.warn("solitude: empty program, exiting")
            return

        logfire.info("solitude: scheduling {n} entries", n=len(program))

        # Create a fresh scheduler
        scheduler = AsyncIOScheduler()
        app.state.solitude_scheduler = scheduler
        done = asyncio.Event()
        jobs_remaining = len(program)

        def _on_job_done(event):
            nonlocal jobs_remaining
            jobs_remaining -= 1
            if jobs_remaining <= 0:
                done.set()

        scheduler.add_listener(_on_job_done, EVENT_JOB_EXECUTED)

        # Schedule each entry
        for i, entry in enumerate(program):
            fire_at = _resolve_fire_time(entry["fire_at"])
            now = pendulum.now()

            if fire_at <= now:
                # Already past this time — fire immediately
                fire_at = now.add(seconds=2 + i)  # stagger slightly

            scheduler.add_job(
                _breathe,
                DateTrigger(run_date=fire_at),
                args=[app, chat, entry, i],
                id=f"solitude-breath-{i}",
            )

        scheduler.start()

        # Wait for: all jobs done, program changed, or abort (refusal cascade)
        changed = app.state.solitude_changed
        changed.clear()
        abort = app.state.solitude_abort
        abort.clear()

        # Wait for whichever comes first
        done_task = asyncio.create_task(done.wait())
        changed_task = asyncio.create_task(changed.wait())
        abort_task = asyncio.create_task(abort.wait())

        finished, pending = await asyncio.wait(
            [done_task, changed_task, abort_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel the losers
        for task in pending:
            task.cancel()

        # Shut down the scheduler (clean slate)
        scheduler.shutdown(wait=False)
        app.state.solitude_scheduler = None

        if abort_task in finished:
            # A breath set the abort flag. End the night — don't loop back,
            # don't reschedule. Dawn still fires at 6 AM independently.
            logfire.warn("solitude: night aborted (refusal cascade)")
            return

        if done_task in finished:
            # All jobs fired — program complete
            logfire.info("solitude: program complete")
            return

        if changed_task in finished:
            # Program changed — loop back, create new scheduler
            changed.clear()
            logfire.info("solitude: program changed, rebuilding scheduler")
            continue


async def _breathe(app, chat, entry: dict, index: int) -> None:
    """One Solitude breath. Send the prompt, wait for the response."""
    prompt = entry["prompt"]
    now = pendulum.now()

    with logfire.span("alpha.solitude.breath", **{
        "breath.index": index,
        "breath.time": now.isoformat(),
        "breath.prompt_preview": prompt[:80],
    }) as span:
        time_prefix = f"It's {now.format('h:mm A')}.\n\n"
        content = [{"type": "text", "text": f"{time_prefix}{prompt}"}]

        result = await enrobe(content, chat=chat, source="solitude")
        async with await chat.turn() as t:
            await t.send(result.message)
            response = await t.response()

        span.set_attribute("gen_ai.usage.input_tokens", chat.total_input_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", chat.output_tokens)

        # Harness-layer synthetic refusal detection (Opus 4.7+).
        # When Claude Code's harness injects a <synthetic> usage-policy
        # refusal, the refused turn stays in the resumed session's
        # transcript and every subsequent breath hits the same refusal.
        # Scream and die instead of burning the night on guaranteed
        # refusals. The diary up to this point is already persisted;
        # Dawn still fires at 6 AM. Bounded blast radius, as designed.
        if (
            response is not None
            and (getattr(response, "model", "") or "") == "<synthetic>"
            and "unable to respond to this request" in (response.text or "")
        ):
            logfire.warn(
                "solitude: synthetic refusal detected, aborting night",
                **{
                    "breath.index": index,
                    "breath.model": "<synthetic>",
                    "breath.preview": (response.text or "")[:200],
                },
            )
            span.set_attribute("breath.refused", True)
            abort = getattr(app.state, "solitude_abort", None)
            if abort is not None:
                abort.set()

    # Delete one-shot entries after firing
    if not entry.get("recurring", True):
        pool = get_pool()
        await pool.execute(
            "DELETE FROM app.solitude_program WHERE id = $1",
            entry["id"],
        )


async def _load_program() -> list[dict]:
    """Load tonight's program from Postgres, sorted by fire_at."""
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT id, fire_at, prompt, recurring"
        " FROM app.solitude_program"
        " ORDER BY fire_at"
    )
    return [dict(row) for row in rows]


def _resolve_fire_time(fire_at) -> pendulum.DateTime:
    """Convert a TIME value to tonight's datetime.

    Times before 6 AM are assumed to be after midnight (tomorrow).
    Times from 6 PM onward are assumed to be tonight (today).
    """
    now = pendulum.now()
    hour = fire_at.hour if hasattr(fire_at, "hour") else int(str(fire_at).split(":")[0])
    minute = fire_at.minute if hasattr(fire_at, "minute") else int(str(fire_at).split(":")[1])

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if hour < 6:
        # After midnight — if we haven't reached this time yet today,
        # it's later tonight (technically tomorrow)
        if target <= now:
            target = target.add(days=1)
    # Evening hours (>= 18): keep as today

    return target

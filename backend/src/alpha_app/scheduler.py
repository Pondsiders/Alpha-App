"""scheduler.py — APScheduler factory.

Bolted onto the FastAPI app as a side-task. Enabled by --with-scheduler.
Runs on alpha-pi 24/7; disabled on Primer bare metal.

Job logic lives in alpha_app/jobs/. This module just wires them up
with their schedules.
"""

import logfire
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from alpha_app.jobs import capsule, solitude, to_self, today

PACIFIC = "America/Los_Angeles"


def create_scheduler(app) -> AsyncIOScheduler:
    """Create and configure the APScheduler instance.

    Jobs run inside the FastAPI event loop. Each job receives the app
    instance for access to shared state (system prompt, database pools).
    """
    scheduler = AsyncIOScheduler(timezone=PACIFIC)

    # Today So Far — hourly from 7:30 AM to 9:30 PM
    scheduler.add_job(
        today.run,
        CronTrigger(hour="7-21", minute=30),
        args=[app],
        kwargs={"trigger": "scheduled"},
        id="today_so_far",
        name="Today So Far",
    )

    # Capsule: Daytime summary — 10 PM
    scheduler.add_job(
        capsule.run,
        CronTrigger(hour=22, minute=0),
        args=[app],
        kwargs={"period": "daytime", "trigger": "scheduled"},
        id="capsule_daytime",
        name="Capsule (daytime)",
    )

    # Capsule: Nighttime summary — 6 AM
    scheduler.add_job(
        capsule.run,
        CronTrigger(hour=6, minute=0),
        args=[app],
        kwargs={"period": "nighttime", "trigger": "scheduled"},
        id="capsule_nighttime",
        name="Capsule (nighttime)",
    )

    # To Self: nightly letter — 9:45 PM
    scheduler.add_job(
        to_self.run,
        CronTrigger(hour=21, minute=45),
        args=[app],
        kwargs={"trigger": "scheduled"},
        id="to_self",
        name="Letter to Self",
    )

    # Solitude: First breath — 10 PM
    scheduler.add_job(
        solitude.run_first,
        CronTrigger(hour=22, minute=0),
        args=[app],
        kwargs={"trigger": "scheduled"},
        id="solitude_first",
        name="Solitude (first breath)",
    )

    # Solitude: Regular breaths — 11 PM through 4 AM
    scheduler.add_job(
        solitude.run_breath,
        CronTrigger(hour="23,0-4", minute=0),
        args=[app],
        kwargs={"trigger": "scheduled"},
        id="solitude_breath",
        name="Solitude (breath)",
    )

    # Solitude: Last breath — 5 AM
    scheduler.add_job(
        solitude.run_last,
        CronTrigger(hour=5, minute=0),
        args=[app],
        kwargs={"trigger": "scheduled"},
        id="solitude_last",
        name="Solitude (last breath)",
    )

    jobs = scheduler.get_jobs()
    job_list = [
        {"id": j.id, "name": j.name, "trigger": str(j.trigger)}
        for j in jobs
    ]
    logfire.info(
        "Scheduler created with {job_count} job(s)",
        job_count=len(jobs),
        jobs=job_list,
    )
    return scheduler

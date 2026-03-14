"""scheduler.py — APScheduler factory.

Bolted onto the FastAPI app as a side-task. Enabled by --with-scheduler.
Runs on alpha-pi 24/7; disabled on Primer bare metal.

Job logic lives in alpha_app/jobs/. This module just wires them up
with their schedules.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from alpha_app.jobs import today

logger = logging.getLogger(__name__)

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

    # Future jobs:
    # scheduler.add_job(capsule.run, CronTrigger(hour=22), args=[app], id="capsule_day")
    # scheduler.add_job(capsule.run, CronTrigger(hour=6), args=[app], id="capsule_night")
    # scheduler.add_job(solitude.run_first, CronTrigger(hour=22), args=[app], id="solitude_first")
    # scheduler.add_job(solitude.run_breath, CronTrigger(hour="23,0-4"), args=[app], id="solitude_breath")
    # scheduler.add_job(solitude.run_last, CronTrigger(hour=5), args=[app], id="solitude_last")

    logger.info("Scheduler created with %d job(s)", len(scheduler.get_jobs()))
    return scheduler

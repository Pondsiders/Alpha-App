"""
Backup sidecar — two jobs, one container.

1. Weekly base backup (Sunday 5:30 AM): pg_basebackup to /mnt/Wilson/basebackup/
2. Hourly B2 sync: aws s3 sync /mnt/Wilson/ to alpha-yellowstone bucket

ZFS lz4 compression handles local storage. B2 is the off-site layer.
"""

import os
import signal
import subprocess
import sys
from datetime import datetime

import logfire
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logfire.configure(service_name="alpha-backup")

BACKUP_DIR = "/mnt/Wilson/basebackup"
WAL_DIR = "/mnt/Wilson/wal"
PG_HOST = os.environ.get("PG_HOST", "localhost")
PG_PORT = os.environ.get("PG_PORT", "5432")
B2_BUCKET = os.environ["B2_BUCKET"]
B2_ENDPOINT = os.environ["B2_ENDPOINT"]


def weekly_basebackup():
    date = datetime.now().strftime("%Y-%m-%d")
    dest = f"{BACKUP_DIR}/base-{date}"

    if os.path.exists(dest):
        logfire.debug("basebackup_skipped", date=date, reason="already exists")
        return

    with logfire.span("weekly_basebackup", date=date):
        try:
            result = subprocess.run(
                ["pg_basebackup",
                 "-h", PG_HOST, "-p", PG_PORT, "-U", "postgres",
                 "-D", dest, "-Ft", "-Xs", "-P"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                logfire.error("basebackup_failed", stderr=result.stderr)
                return
            size_mb = sum(
                f.stat().st_size for f in __import__("pathlib").Path(dest).iterdir()
            ) / 1024 / 1024
            logfire.info("basebackup_complete", size_mb=round(size_mb, 1))
        except Exception as e:
            logfire.error("basebackup_error", error=str(e))


def sync_to_b2():
    with logfire.span("b2_sync"):
        try:
            result = subprocess.run(
                ["aws", "s3", "sync", "/mnt/Wilson/", f"s3://{B2_BUCKET}/",
                 "--endpoint-url", B2_ENDPOINT,
                 "--only-show-errors"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                logfire.error("b2_sync_failed", stderr=result.stderr)
                return
            logfire.info("b2_sync_complete")
        except Exception as e:
            logfire.error("b2_sync_error", error=str(e))


def main():
    logfire.info("backup_sidecar_starting",
                 basebackup_dir=BACKUP_DIR,
                 b2_bucket=B2_BUCKET)

    scheduler = BlockingScheduler(timezone=os.environ.get("TZ", "America/Los_Angeles"))

    scheduler.add_job(
        weekly_basebackup,
        CronTrigger(day_of_week="sun", hour=5, minute=30),
        id="weekly_basebackup",
        name="Weekly base backup",
        max_instances=1,
    )

    scheduler.add_job(
        sync_to_b2,
        CronTrigger(minute=17),
        id="b2_sync",
        name="Hourly B2 sync",
        max_instances=1,
    )

    # Run both once at startup
    scheduler.add_job(weekly_basebackup, id="initial_basebackup")
    scheduler.add_job(sync_to_b2, id="initial_sync")

    def shutdown(signum, frame):
        logfire.info("backup_sidecar_stopping", signal=signum)
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logfire.info("backup_sidecar_stopped")


if __name__ == "__main__":
    main()

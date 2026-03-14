"""jobs/ — Background jobs that run on a schedule or manually.

Each job module exports:
    run(app, **kwargs)  — async function, called by APScheduler or CLI
    add_cli_args(sub)   — adds job-specific arguments to argparse subparser

Usage:
    # Scheduled (via APScheduler in the FastAPI lifespan):
    scheduler.add_job(today.run, args=[app], ...)

    # Manual (via CLI):
    uv run job today-so-far
    uv run job capsule --daypart daytime --date 2026-03-13
"""

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env the same way main.py does
load_dotenv(Path(__file__).resolve().parents[4] / ".env")


def cli() -> None:
    """Entry point for `uv run job <name> [args]`."""
    parser = argparse.ArgumentParser(
        prog="job",
        description="Run Alpha background jobs manually.",
    )
    subparsers = parser.add_subparsers(dest="job_name", help="Job to run")

    # Register each job's subcommand
    from alpha_app.jobs import today
    today.add_cli_args(subparsers)

    # Future:
    # from alpha_app.jobs import capsule
    # capsule.add_cli_args(subparsers)

    args = parser.parse_args()

    if not args.job_name:
        parser.print_help()
        sys.exit(1)

    # Run the job
    asyncio.run(args.func(args))

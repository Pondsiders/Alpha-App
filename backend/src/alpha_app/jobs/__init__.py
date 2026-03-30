"""jobs/ — Background jobs that run on a schedule or manually.

Each job module exports:
    run(app, **kwargs)  — async function, called by APScheduler or CLI

The circadian chain: Dawn -> Dusk -> Solitude -> Dawn.
Each job does its work, then schedules its successor.
Jobs persist in app.jobs (Postgres). APScheduler is the in-memory executor.
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

    # Register each job's subcommand (only those with CLI support)
    from alpha_app.jobs import dawn
    dawn.add_cli_args(subparsers) if hasattr(dawn, "add_cli_args") else None

    args = parser.parse_args()

    if not args.job_name:
        parser.print_help()
        sys.exit(1)

    # Run the job
    asyncio.run(args.func(args))

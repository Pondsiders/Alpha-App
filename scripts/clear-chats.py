#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "asyncpg>=0.30.0",
#     "python-dotenv>=1.0.0",
# ]
# ///
"""Clear the chats table. For development use.

Usage:
    ./scripts/clear-chats.py          # truncate with confirmation
    ./scripts/clear-chats.py --yes    # skip confirmation
"""

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv


async def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    # Show where we're connecting so there are no surprises
    host = dsn.split("@")[-1].split("/")[0] if "@" in dsn else "local"
    print(f"Connecting to: {host}")

    conn = await asyncpg.connect(dsn)
    try:
        count = await conn.fetchval("SELECT count(*) FROM app.chats")
        print(f"Found {count} chat(s) in app.chats")

        if count == 0:
            print("Nothing to clear.")
            return

        if "--yes" not in sys.argv:
            answer = input(f"TRUNCATE {count} chat(s)? [y/N] ")
            if answer.lower() not in ("y", "yes"):
                print("Aborted.")
                return

        await conn.execute("TRUNCATE app.chats")
        print("Done. Table cleared.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

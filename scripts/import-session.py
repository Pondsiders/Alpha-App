#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "asyncpg>=0.30.0",
#     "python-dotenv>=1.0.0",
# ]
# ///
"""Import a Claude Code session into Alpha-App.

Copies the JSONL transcript and inserts a chat row so Alpha-App can
replay and resume the conversation.

Usage:
    ./scripts/import-session.py <session-uuid>
    ./scripts/import-session.py <session-uuid> --title "My chat"
    ./scripts/import-session.py <session-uuid> --source-dir ~/.claude/projects/-Other

The session UUID is the filename (minus .jsonl) of a Claude Code transcript.
Default source is ~/.claude/projects/-Pondside/.
"""

import asyncio
import os
import secrets
import shutil
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

# -- Constants (mirror alpha_app.constants) ------------------------------------

CLAUDE_CONFIG_DIR = Path("/Pondside/Alpha-Home/.claude")
CLAUDE_CWD = Path("/Pondside")

# Where Alpha-App expects session JSONL files
_formatted_cwd = str(CLAUDE_CWD.resolve()).replace("/", "-")
DEST_DIR = CLAUDE_CONFIG_DIR / "projects" / _formatted_cwd

# Default source: Duckpond / Claude Code sessions on Primer
DEFAULT_SOURCE_DIR = Path.home() / ".claude" / "projects" / "-Pondside"


def generate_chat_id() -> str:
    """Same as alpha_app.chat.generate_chat_id."""
    return secrets.token_urlsafe(9)


def parse_args(argv: list[str]) -> tuple[str, str | None, Path]:
    """Parse CLI args. Returns (uuid, title, source_dir)."""
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        sys.exit(0)

    uuid = argv[1]
    title: str | None = None
    source_dir = DEFAULT_SOURCE_DIR

    i = 2
    while i < len(argv):
        if argv[i] == "--title" and i + 1 < len(argv):
            title = argv[i + 1]
            i += 2
        elif argv[i] == "--source-dir" and i + 1 < len(argv):
            source_dir = Path(argv[i + 1])
            i += 2
        else:
            print(f"Unknown argument: {argv[i]}", file=sys.stderr)
            sys.exit(1)

    return uuid, title, source_dir


async def main() -> None:
    session_uuid, title, source_dir = parse_args(sys.argv)

    # Load DATABASE_URL
    env_file = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(env_file, override=True)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set (check .env)", file=sys.stderr)
        sys.exit(1)

    # Find source JSONL
    source = source_dir / f"{session_uuid}.jsonl"
    if not source.exists():
        print(f"Not found: {source}", file=sys.stderr)
        sys.exit(1)

    lines = sum(1 for _ in open(source))
    print(f"Source:  {source} ({lines} lines)")

    # Copy to Alpha-App's session directory
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    dest = DEST_DIR / f"{session_uuid}.jsonl"

    if dest.exists():
        print(f"Already exists: {dest}")
        print("  (skipping copy)")
    else:
        shutil.copy2(source, dest)
        print(f"Copied: {dest}")

    # Check if this session is already imported
    conn = await asyncpg.connect(dsn)
    try:
        existing = await conn.fetchval(
            "SELECT id FROM app.chats WHERE data->>'session_uuid' = $1",
            session_uuid,
        )
        if existing:
            print(f"\nAlready imported as chat {existing}")
            print("Nothing to do.")
            return

        # Generate chat ID and insert
        chat_id = generate_chat_id()
        import time
        created_at = int(time.time())

        await conn.execute(
            """
            INSERT INTO app.chats (id, data) VALUES ($1, $2::jsonb)
            """,
            chat_id,
            f'{{"session_uuid": "{session_uuid}", "title": "{title or ""}", "created_at": {created_at}, "token_count": 0, "context_window": 200000}}',
        )

        print(f"\nImported:")
        print(f"  Chat ID:      {chat_id}")
        print(f"  Session UUID: {session_uuid}")
        if title:
            print(f"  Title:        {title}")
        print(f"\nOpen Alpha-App and look for it in the sidebar.")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

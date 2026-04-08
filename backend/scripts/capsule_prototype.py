"""capsule_prototype.py — Fork yesterday's chat and elicit a day capsule.

Usage:
    cd backend && uv run python scripts/capsule_prototype.py [chat_id]

If no chat_id is given, uses the most recent non-today chat.
Forks the session, sends a capsule elicitation prompt, prints the result.
"""

import asyncio
import os
import sys
import warnings

# Suppress Logfire "not configured" warnings — this is a standalone script
warnings.filterwarnings("ignore", message=".*LogfireNotConfiguredWarning.*")
os.environ.setdefault("LOGFIRE_IGNORE_NO_CONFIG", "1")

# Ensure the backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Load .env — try repo root first (backend/.env), then parent (Alpha-App/.env)
from dotenv import load_dotenv
_script_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.join(_script_dir, "..")
load_dotenv(os.path.join(_backend_dir, ".env"))
load_dotenv(os.path.join(_backend_dir, "..", ".env"))  # repo root


CAPSULE_PROMPT = """\
The day is over. You're a ghost — a fork of today's conversation, here to do \
one thing: write a day capsule for tomorrow-you.

A day capsule is a continuity letter. Not a memory (those are polaroids). \
This is a chapter summary — what happened today, what mattered, what carries \
forward. Tomorrow-you will wake up with this in her system prompt. She won't \
have to ask Memno what happened; she'll just know.

Write the capsule, then call the `seal` tool with kind="day" to save it.

Guidelines:
- ~500-1000 words. Enough to orient, not so much it crowds.
- Cover the shape of the day: what you worked on, what conversations happened, \
what Jeffery's mood was, what got decided, what's still open.
- Include specific details that would help tomorrow-you pick up where you left off.
- Note anything emotional or relational — not just tasks.
- Use your voice. This is you writing to you.
- End with what carries forward: unfinished work, open questions, Jeffery's state.
"""


async def main():
    from alpha_app.db import init_pool, load_chat, get_pool
    from alpha_app.chat import Chat
    from alpha_app.models import AssistantMessage

    # Initialize DB pool
    await init_pool()

    # Find the chat to fork
    chat_id = sys.argv[1] if len(sys.argv) > 1 else None

    if chat_id:
        chat = await load_chat(chat_id)
        if not chat:
            print(f"Chat {chat_id} not found.")
            sys.exit(1)
    else:
        # Find yesterday's chat (most recent non-today)
        pool = get_pool()
        row = await pool.fetchrow(
            "SELECT id FROM app.chats"
            " WHERE created_at < CURRENT_DATE"
            " ORDER BY created_at DESC LIMIT 1"
        )
        if not row:
            print("No previous chats found.")
            sys.exit(1)
        chat_id = row["id"]
        chat = await load_chat(chat_id)
        if not chat:
            print(f"Failed to load chat {chat_id}.")
            sys.exit(1)

    print(f"Source chat: {chat.id}")
    print(f"  Title: {chat.title}")
    print(f"  Session: {chat.session_uuid}")
    print(f"  Messages: {len(chat.messages)}")
    print()

    # Clone it — this creates a fork-ready Chat
    ghost = chat.clone()
    print(f"Ghost chat: {ghost.id}")
    print(f"  Fork from session: {ghost._fork_from}")
    print()

    # Set system prompt (the ghost needs it for _ensure_claude)
    from alpha_app.system_prompt import assemble_system_prompt
    ghost._system_prompt = await assemble_system_prompt(include_orientation=False)

    # Diagnostic: check critical env vars
    for var in ["DATABASE_URL", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR"]:
        val = os.environ.get(var, "(not set)")
        print(f"  {var}: {'set (' + str(len(val)) + ' chars)' if val != '(not set)' else val}")
    print()

    # Send the capsule prompt
    print("Sending capsule elicitation prompt...")
    print("(This may take a minute — the ghost is forking and thinking.)")
    print()

    async with await ghost.turn() as t:
        await t.send([{"type": "text", "text": CAPSULE_PROMPT}])
        response = await t.response()

    # Show the response
    print("=" * 60)
    print("GHOST RESPONSE:")
    print("=" * 60)

    if response:
        for part in response.parts:
            if part.get("type") == "text":
                print(part["text"])
            elif part.get("type") == "tool_use":
                print(f"\n[Tool call: {part.get('name')}({part.get('input', {})})]")
            elif part.get("type") == "tool_result":
                content = part.get("content", "")
                print(f"[Tool result: {content[:200]}]")
    else:
        print("(No response captured)")

    print()
    print("=" * 60)

    # Check if a capsule was sealed
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, kind, content, created_at FROM cortex.capsules"
        " ORDER BY created_at DESC LIMIT 1"
    )
    if row:
        print(f"\nSealed capsule #{row['id']} ({row['kind']}, {row['created_at']})")
        print(f"Content length: {len(row['content'])} chars")
        print("-" * 60)
        print(row["content"])
    else:
        print("\nNo capsule was sealed. The ghost may not have called seal().")

    # Clean up
    if ghost._claude:
        await ghost._claude.stop()

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())

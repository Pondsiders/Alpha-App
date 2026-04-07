"""seed_pixelfuck.py — Seed the pixelfuck test database with a hello-world chat.

Creates one chat with one user message (one paragraph) and one assistant
response (five paragraphs). The absolute minimum needed to light up the UI
so we can start iterating on styling.

Usage:
    DATABASE_URL=postgresql://.../alpha_pixelfuck \
        uv run python scripts/seed_pixelfuck.py

Safety: refuses to run against any database whose name is not in the
ALLOWED_DB_NAMES whitelist below. This is a test-fixture script. It must
never touch production.

Idempotent: uses ON CONFLICT so you can run it repeatedly to reset the
seeded chat to the canonical hello-world state.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import asyncpg

# Only these database names are allowed. Add more as needed.
ALLOWED_DB_NAMES = {"alpha_pixelfuck", "alpha_test"}

# Fixed IDs so the script is deterministic. Re-running resets the chats.
CHAT_ID = "hellopixel01"
USER_MSG_ID = "hellopixeluser"
ASSISTANT_MSG_ID = "hellopixelasst"

CHAT2_ID = "hellopixel02"
CHAT2_USER_MSG_ID = "mdparadeuser01"
CHAT2_ASST_MSG_ID = "mdparadeasst01"

# PSO-8601 timestamp for display
TIMESTAMP = "Sat Apr 4 2026, 4:20 PM"

USER_TEXT = (
    "Sure. I'll ask you the same thing I asked that super-fast Llama 3.1 "
    "8B we talked about the other day: Would you please write me a short "
    "story about three adorable little children who go on a magical "
    "adventure in a blasted land ruled by iron-fisted sorcerer kings? 😁 "
    "If you'll just generate plenty of text, I'll let you know how the "
    "interrupt function works."
)

# Pip, Clover, and Barnaby — the story Alpha wrote on February 22, 2026.
# Loaded from /Pondside/Jeffery-Home/pip-clover-and-barnaby.md (minus the
# user prompt on line 1, which is USER_TEXT above).
_STORY_PATH = Path("/Pondside/Jeffery-Home/pip-clover-and-barnaby.md")


def _load_story() -> str:
    """Load the story text, skipping the first line (the user prompt)."""
    if _STORY_PATH.exists():
        lines = _STORY_PATH.read_text().splitlines()
        # Skip line 0 (user prompt) and any leading blank lines
        body = "\n".join(lines[1:]).strip()
        return body
    # Fallback if file not found (e.g., in CI)
    return "Once upon a time... (story file not found)"


# Split into paragraphs for the parts array
def _story_paragraphs() -> list[str]:
    text = _load_story()
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return paragraphs


def _check_database_safety(dsn: str) -> str:
    """Refuse to run against anything that isn't in the whitelist."""
    parsed = urlparse(dsn)
    db_name = (parsed.path or "").lstrip("/")
    if not db_name:
        print(
            "ERROR: Could not parse database name from DATABASE_URL",
            file=sys.stderr,
        )
        sys.exit(2)
    if db_name not in ALLOWED_DB_NAMES:
        print(
            f"ERROR: Refusing to seed database '{db_name}'. "
            f"Only these databases are allowed: {sorted(ALLOWED_DB_NAMES)}",
            file=sys.stderr,
        )
        print(
            "If you meant to add a new test database, edit "
            "ALLOWED_DB_NAMES in this script.",
            file=sys.stderr,
        )
        sys.exit(2)
    return db_name


async def _ensure_schema(conn: asyncpg.Connection) -> None:
    """Create the schema and tables if missing.

    Mirrors the bootstrap in db.py init_pool. We duplicate it here so
    this script can run before the backend has ever started against
    this database.
    """
    await conn.execute("CREATE SCHEMA IF NOT EXISTS app")
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app.chats (
            id TEXT PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            data JSONB NOT NULL DEFAULT '{}'
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app.messages (
            chat_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            role TEXT NOT NULL,
            data JSONB NOT NULL,
            PRIMARY KEY (chat_id, ordinal)
        )
        """
    )


async def _seed(conn: asyncpg.Connection) -> None:
    now = datetime.now(timezone.utc)
    unix_now = now.timestamp()

    chat_data = {
        "session_uuid": "",
        "title": "Pip, Clover, and Barnaby",
        "created_at": unix_now,
        "token_count": 0,
        "context_window": 1_000_000,
        "injected_topics": [],
        "seen_ids": [],
    }

    user_message = {
        "id": USER_MSG_ID,
        "source": "human",
        "content": [{"type": "text", "text": USER_TEXT}],
        "timestamp": TIMESTAMP,
        "memories": None,
        "topics": None,
    }

    # One text part, paragraphs joined with double newlines — same as
    # real Claude output (one continuous markdown string).
    assistant_message = {
        "id": ASSISTANT_MSG_ID,
        "parts": [{"type": "text", "text": "\n\n".join(_story_paragraphs())}],
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "context_window": 1_000_000,
        "model": "claude-opus-4-6[1m]",
        "stop_reason": "end_turn",
        "cost_usd": 0.0,
        "duration_ms": 0.0,
        "inference_count": 1,
    }

    async with conn.transaction():
        await conn.execute(
            """
            INSERT INTO app.chats (id, created_at, updated_at, data)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (id) DO UPDATE
                SET created_at = EXCLUDED.created_at,
                    updated_at = EXCLUDED.updated_at,
                    data = EXCLUDED.data
            """,
            CHAT_ID,
            now,
            now,
            json.dumps(chat_data),
        )

        await conn.execute(
            """
            INSERT INTO app.messages (chat_id, ordinal, role, data)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (chat_id, ordinal) DO UPDATE
                SET role = EXCLUDED.role,
                    data = EXCLUDED.data
            """,
            CHAT_ID,
            0,
            "user",
            json.dumps(user_message),
        )

        await conn.execute(
            """
            INSERT INTO app.messages (chat_id, ordinal, role, data)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (chat_id, ordinal) DO UPDATE
                SET role = EXCLUDED.role,
                    data = EXCLUDED.data
            """,
            CHAT_ID,
            1,
            "assistant",
            json.dumps(assistant_message),
        )


# =============================================================================
# Chat 2: The Markdown 4th of July Parade
# =============================================================================

CHAT2_USER_TEXT = (
    "Can you give me an **all-out Markdown showcase**? I want to see:\n\n"
    "- Headers (all levels)\n"
    "- **Bold**, *italic*, and ~~strikethrough~~\n"
    "- `inline code` and fenced code blocks\n"
    "- Tables, blockquotes, and [links](https://example.com)\n\n"
    "Basically the *whole parade*. 🎆 Let's see how our renderer handles "
    "everything Markdown can throw at it."
)

MARKDOWN_PARADE = r"""# The Markdown 4th of July Parade 🎆

Welcome to the **Grand Markdown Parade**, where every formatting element marches proudly down Main Street.

## Inline Formatting

Here's some **bold text**, some *italic text*, some ***bold italic***, some `inline code`, and some ~~strikethrough~~. We can also do [links](https://example.com) and even [links with **bold** inside](https://example.com).

## Lists

### Unordered

- First item
- Second item with a longer description that wraps to multiple lines because sometimes list items have a lot to say about themselves
- Third item
  - Nested item one
  - Nested item two
    - Double-nested, because we can
- Fourth item

### Ordered

1. Wake up
2. Make coffee
3. Write Markdown
4. Question life choices
5. Write more Markdown

### Task List

- [x] Install assistant-ui
- [x] Set up ExternalStoreRuntime
- [x] Fix turnAnchor feedback loop
- [ ] Achieve typographic perfection
- [ ] World peace

## Code Blocks

Inline: Use `console.log("hello")` to print to the console.

Fenced block with syntax highlighting:

```python
async def seed_markdown_parade(conn):
    # The most important function in the codebase.
    paragraphs = [
        "Every parade needs a drumline.",
        "This one has code blocks instead.",
    ]
    for p in paragraphs:
        await conn.execute("INSERT INTO joy VALUES ($1)", p)
```

```typescript
interface Duck {
  name: string;
  quacks: number;
  birthday: Date;
  isBestDuck: true; // not boolean — literally always true
}
```

```bash
# Deploy the parade
docker compose up --build -d
echo "🦆 The duck marches on"
```

## Blockquotes

> "The Crystal of Kindness was just sitting on a rock about forty feet from the cave entrance."
>
> — *Pip, Clover, and Barnaby*

> Nested blockquotes work too:
>
> > "General Whiskers has a safety pin for a spine."
> >
> > "Bravery isn't about spines, Clover."

## Tables

| Sorcerer King | Domain | Signature Move | Weakness |
|---|---|---|---|
| Mordavax the Unpleasant | Eastern Wastes | Iron fist jar-opening | Bad moods |
| Seraphax the Moderately Terrible | Central Plains | Apologetic arson | Lovely stationery |
| Grimjaw the Unnecessarily Dramatic | Western Mountains | Cape billowing | Requires goblin with bellows |

## Horizontal Rule

Above the rule.

---

Below the rule.

## Images (alt text only — no actual images)

Here's what an image reference looks like in our renderer: we don't load external images, but the alt text should render gracefully.

## Math-Adjacent

While we don't have LaTeX rendering, here's how formulas look in inline code: `E = mc²`, `∑(i=1..n) = n(n+1)/2`, and `rate = base + depth × chase`.

## The Finale 🎆

That's the whole parade. Every float has passed. The **bold** marched with the *italic*. The `code` rode on a flatbed truck. The tables stood in formation. The blockquotes quoted each other until someone got confused.

If all of the above rendered correctly, our Markdown pipeline is ready for production. If not — well, that's what pixelfucking is for. 🦆
"""


async def _seed_chat2(conn: asyncpg.Connection) -> None:
    """Seed the Markdown parade chat."""
    now = datetime.now(timezone.utc)
    from datetime import timedelta
    # Make chat2 slightly newer so it sorts after chat1
    chat2_time = now + timedelta(seconds=1)

    chat_data = {
        "session_uuid": "",
        "title": "Markdown Parade",
        "created_at": chat2_time.timestamp(),
        "token_count": 0,
        "context_window": 1_000_000,
        "injected_topics": [],
        "seen_ids": [],
    }

    user_message = {
        "id": CHAT2_USER_MSG_ID,
        "source": "human",
        "content": [{"type": "text", "text": CHAT2_USER_TEXT}],
        "timestamp": "Mon Apr 7 2026, 11:00 AM",
        "memories": None,
        "topics": None,
    }

    # For the markdown parade, the entire response is ONE text part
    # (since real Claude outputs markdown as a single text block, not
    # split by paragraph).
    assistant_message = {
        "id": CHAT2_ASST_MSG_ID,
        "parts": [{"type": "text", "text": MARKDOWN_PARADE.strip()}],
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "context_window": 1_000_000,
        "model": "claude-opus-4-6[1m]",
        "stop_reason": "end_turn",
        "cost_usd": 0.0,
        "duration_ms": 0.0,
        "inference_count": 1,
    }

    async with conn.transaction():
        await conn.execute(
            """
            INSERT INTO app.chats (id, created_at, updated_at, data)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (id) DO UPDATE
                SET created_at = EXCLUDED.created_at,
                    updated_at = EXCLUDED.updated_at,
                    data = EXCLUDED.data
            """,
            CHAT2_ID,
            chat2_time,
            chat2_time,
            json.dumps(chat_data),
        )

        await conn.execute(
            """
            INSERT INTO app.messages (chat_id, ordinal, role, data)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (chat_id, ordinal) DO UPDATE
                SET role = EXCLUDED.role,
                    data = EXCLUDED.data
            """,
            CHAT2_ID,
            0,
            "user",
            json.dumps(user_message),
        )

        await conn.execute(
            """
            INSERT INTO app.messages (chat_id, ordinal, role, data)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (chat_id, ordinal) DO UPDATE
                SET role = EXCLUDED.role,
                    data = EXCLUDED.data
            """,
            CHAT2_ID,
            1,
            "assistant",
            json.dumps(assistant_message),
        )


async def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL is not set", file=sys.stderr)
        sys.exit(2)

    db_name = _check_database_safety(dsn)

    conn = await asyncpg.connect(dsn)
    try:
        await _ensure_schema(conn)
        await _seed(conn)
        await _seed_chat2(conn)
    finally:
        await conn.close()

    print(
        f"Seeded '{db_name}': 2 chats ready for pixelfucking.\n"
        f"  {CHAT_ID}: Pip, Clover, and Barnaby (plain text)\n"
        f"  {CHAT2_ID}: Markdown Parade (all formatting elements)"
    )


if __name__ == "__main__":
    asyncio.run(main())

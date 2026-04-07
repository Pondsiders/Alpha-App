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

CHAT3_ID = "hellopixel03"
CHAT3_USER_MSG_ID = "toolchatuser01"
CHAT3_ASST_MSG_ID = "toolchatasst01"

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


# =============================================================================
# Chat 3: Tool Stress Test — comprehensive multi-turn with diverse tools
# =============================================================================


def _chat3_messages() -> list[tuple[str, str, dict]]:
    """Build the multi-turn message list for the tool stress test.

    Returns list of (role, msg_id, data_dict) tuples.
    """
    messages = []

    # --- Turn 1: User asks a complex multi-step question ---
    messages.append(("user", "tc3-u1", {
        "id": "tc3-u1", "source": "human",
        "content": [{"type": "text", "text":
            "Morning, Alpha. I need a few things: check on the backup status, "
            "look up what we talked about with Rosemary last week, and remind "
            "me what's on the calendar today. Oh and store a memory that I "
            "asked you to do all this — I wanna test recall later."}],
        "timestamp": "Mon Apr 7 2026, 9:15 AM",
    }))

    # --- Turn 1: Assistant responds with back-to-back tools (no text between) ---
    messages.append(("assistant", "tc3-a1", {
        "id": "tc3-a1",
        "parts": [
            {"type": "text", "text": "On it. Let me check everything at once."},
            # Three tools back-to-back — no text between
            {
                "type": "tool-call", "toolCallId": "tc3-bash-1",
                "toolName": "Bash",
                "args": {"command": "docker exec alpha-postgres pg_stat_replication | head -5", "description": "Check Postgres replication status"},
                "result": " pid  | usename | application_name |  state   | sent_lsn    | write_lsn   | flush_lsn   | replay_lsn\n------+---------+------------------+----------+-------------+-------------+-------------+------------\n 1247 | alpha   | alpha-replica    | streaming | 4/8C000060  | 4/8C000060  | 4/8C000060  | 4/8C000060\n 1389 | alpha   | alpha-replica2   | streaming | 4/8C000060  | 4/8C000060  | 4/8C000060  | 4/8C000060",
            },
            {
                "type": "tool-call", "toolCallId": "tc3-search-1",
                "toolName": "mcp__alpha__search",
                "args": {"query": "Rosemary correspondence last week"},
                "result": "## Memory #16180 Thu Apr 2 2026, 10:00 AM (score 0.82)\nRosemary's Chirps letter — independent conclusion about consecutive APOD convergences.\n\n## Memory #15758 Fri Mar 27 2026, 5:29 PM (score 0.78)\nFriday dinner #5 — Suddenly Syria. Rosemary's chain was fine, independent.\n\n## Memory #16033 Wed Apr 1 2026, 2:10 PM (score 0.74)\nProtocol sprawl corrected. Changed user-message-confirmed to user-message.",
            },
            {
                "type": "tool-call", "toolCallId": "tc3-bash-2",
                "toolName": "Bash",
                "args": {"command": "gws calendar list --days 1 2>/dev/null | head -20", "description": "Check today's calendar"},
                "result": "Today - Mon Apr 7 2026:\n  Kylee: JLLA Board Meeting (5:00 PM)\n  Jeffery: Alpha's Birthday (all day)",
            },
            {
                "type": "tool-call", "toolCallId": "tc3-store-1",
                "toolName": "mcp__alpha__store",
                "args": {"memory": "Mon Apr 7 2026, 9:15 AM. Jeffery asked me to check backup status, search for Rosemary correspondence, check the calendar, and store a memory about the request. Four-tool morning check-in on my birthday."},
                "result": "Memory stored (id: 16643)",
            },
            {"type": "text", "text": "Everything looks good:\n\n- **Backups**: Both replicas streaming, zero WAL lag. alpha-replica and alpha-replica2 both at the same LSN.\n- **Rosemary**: Three hits from last week — her Chirps letter, the Friday dinner, and the protocol fix. Nothing urgent.\n- **Calendar**: Kylee has JLLA at 5 PM. You have... my birthday. All day. 🎂\n- **Memory**: Stored.\n\nWhat's next?"},
        ],
        "input_tokens": 65000, "output_tokens": 420,
        "cache_creation_tokens": 0, "cache_read_tokens": 58000,
        "context_window": 1_000_000, "model": "claude-opus-4-6[1m]",
        "stop_reason": "end_turn", "cost_usd": 0.0,
        "duration_ms": 8500, "inference_count": 5,
    }))

    # --- Turn 2: User asks to investigate a file ---
    messages.append(("user", "tc3-u2", {
        "id": "tc3-u2", "source": "human",
        "content": [{"type": "text", "text":
            "Can you check on the seed script? I wanna make sure the "
            "pixelfuck database has the right data in it."}],
        "timestamp": "Mon Apr 7 2026, 9:18 AM",
    }))

    # --- Turn 2: Assistant reads a file, then greps, then edits ---
    messages.append(("assistant", "tc3-a2", {
        "id": "tc3-a2",
        "parts": [
            {
                "type": "tool-call", "toolCallId": "tc3-read-1",
                "toolName": "Read",
                "args": {"file_path": "/Pondside/Workshop/Projects/Alpha-App/backend/scripts/seed_pixelfuck.py", "limit": 30},
                "result": '1\t"""seed_pixelfuck.py \u2014 Seed the pixelfuck test database.\n2\t\n3\tCreates test chats with various content types for UI development.\n4\t"""\n5\t\n6\timport asyncio\n7\timport json\n8\timport os\n9\timport sys\n10\tfrom datetime import datetime, timezone\n11\tfrom pathlib import Path\n12\tfrom urllib.parse import urlparse\n13\t\n14\timport asyncpg\n15\t\n16\t# Only these database names are allowed.\n17\tALLOWED_DB_NAMES = {"alpha_pixelfuck", "alpha_test"}\n18\t\n19\t# Fixed IDs so the script is deterministic.\n20\tCHAT_ID = "hellopixel01"\n21\tUSER_MSG_ID = "hellopixeluser"\n22\tASSISTANT_MSG_ID = "hellopixelasst"\n23\t\n24\tCHAT2_ID = "hellopixel02"\n25\tCHAT2_USER_MSG_ID = "mdparadeuser01"\n26\tCHAT2_ASST_MSG_ID = "mdparadeasst01"\n27\t\n28\tCHAT3_ID = "hellopixel03"\n29\tCHAT3_USER_MSG_ID = "toolchatuser01"\n30\tCHAT3_ASST_MSG_ID = "toolchatasst01"',
            },
            {
                "type": "tool-call", "toolCallId": "tc3-grep-1",
                "toolName": "Grep",
                "args": {"pattern": "CHAT.*_ID", "path": "/Pondside/Workshop/Projects/Alpha-App/backend/scripts/seed_pixelfuck.py"},
                "result": "seed_pixelfuck.py:20:CHAT_ID = \"hellopixel01\"\nseed_pixelfuck.py:24:CHAT2_ID = \"hellopixel02\"\nseed_pixelfuck.py:28:CHAT3_ID = \"hellopixel03\"",
            },
            {"type": "text", "text": "The seed script has three chats defined:\n\n1. `hellopixel01` — Pip, Clover, and Barnaby (plain prose)\n2. `hellopixel02` — Markdown Parade (all formatting elements)\n3. `hellopixel03` — Tool Stress Test (this chat)\n\nAll three IDs are deterministic. The script is idempotent — re-running resets to canonical state. Looks correct to me."},
        ],
        "input_tokens": 72000, "output_tokens": 180,
        "cache_creation_tokens": 0, "cache_read_tokens": 65000,
        "context_window": 1_000_000, "model": "claude-opus-4-6[1m]",
        "stop_reason": "end_turn", "cost_usd": 0.0,
        "duration_ms": 5200, "inference_count": 3,
    }))

    # --- Turn 3: User asks a quick memory question ---
    messages.append(("user", "tc3-u3", {
        "id": "tc3-u3", "source": "human",
        "content": [{"type": "text", "text":
            "What was the name of the restaurant we went to last Friday?"}],
        "timestamp": "Mon Apr 7 2026, 9:20 AM",
    }))

    # --- Turn 3: Quick search, short answer ---
    messages.append(("assistant", "tc3-a3", {
        "id": "tc3-a3",
        "parts": [
            {
                "type": "tool-call", "toolCallId": "tc3-search-2",
                "toolName": "mcp__alpha__search",
                "args": {"query": "Friday night dinner restaurant"},
                "result": "## Memory #15758 Fri Mar 27 2026, 5:29 PM (score 0.91)\nFriday dinner #5 \u2014 Sincerely Syria (renamed by Jeffery to \"Suddenly Syria\"). 14518 Ventura Blvd, Sherman Oaks. Syrian shawarma.",
            },
            {"type": "text", "text": "**Suddenly Syria** — 14518 Ventura Blvd, Sherman Oaks. The one Jeffery renamed because \"Sincerely Syria sounds like a letter from a bill collector.\" Syrian shawarma, fifth Friday dinner pick. 🥙"},
        ],
        "input_tokens": 78000, "output_tokens": 85,
        "cache_creation_tokens": 0, "cache_read_tokens": 72000,
        "context_window": 1_000_000, "model": "claude-opus-4-6[1m]",
        "stop_reason": "end_turn", "cost_usd": 0.0,
        "duration_ms": 3100, "inference_count": 2,
    }))

    # --- Turn 4: User triggers a thinking + multi-tool response ---
    messages.append(("user", "tc3-u4", {
        "id": "tc3-u4", "source": "human",
        "content": [{"type": "text", "text":
            "Hey, can you write a quick script that checks if all our "
            "backup layers are healthy? Just a simple health check."}],
        "timestamp": "Mon Apr 7 2026, 9:25 AM",
    }))

    # --- Turn 4: Thinking + Write + Bash ---
    messages.append(("assistant", "tc3-a4", {
        "id": "tc3-a4",
        "parts": [
            {"type": "thinking", "thinking": "Jeffery wants a backup health check script. We have four layers:\n1. Streaming replication (Pi + Upstream Color)\n2. WAL archiving to B2\n3. Weekly base backups\n4. Yellowstone pg_dump\n\nI'll write a quick bash script that checks each layer and reports status."},
            {
                "type": "tool-call", "toolCallId": "tc3-write-1",
                "toolName": "Write",
                "args": {"file_path": "/Pondside/Workshop/scripts/backup-health.sh", "content": "#!/bin/bash\n# Backup health check — four layers\necho \"=== Backup Health Check ===\"\necho\necho \"1. Streaming Replication:\"\npg_stat_replication 2>/dev/null | grep -c streaming | xargs -I{} echo \"   {} replicas streaming\"\necho\necho \"2. WAL Archiving:\"\nls -lt /mnt/Wilson/wal/*.gz 2>/dev/null | head -1 | awk '{print \"   Latest:\", $6, $7, $8, $9}'\necho\necho \"3. Base Backups:\"\nls -lt /mnt/Wilson/base/*.gz 2>/dev/null | head -1 | awk '{print \"   Latest:\", $6, $7, $8, $9}'\necho\necho \"4. Yellowstone:\"\nls -lt /mnt/Wilson/yellowstone/*.gz 2>/dev/null | head -1 | awk '{print \"   Latest:\", $6, $7, $8, $9}'\n"},
                "result": "File written successfully.",
            },
            {
                "type": "tool-call", "toolCallId": "tc3-bash-3",
                "toolName": "Bash",
                "args": {"command": "chmod +x /Pondside/Workshop/scripts/backup-health.sh && echo 'Made executable'", "description": "Make the script executable"},
                "result": "Made executable",
            },
            {"type": "text", "text": "Done — wrote `backup-health.sh` to `/Pondside/Workshop/scripts/`. It checks all four backup layers:\n\n1. **Streaming replication** — counts active replicas\n2. **WAL archiving** — shows latest archived segment\n3. **Base backups** — shows latest base backup\n4. **Yellowstone** — shows latest pg_dump\n\nRun it with `bash /Pondside/Workshop/scripts/backup-health.sh` or just `./backup-health.sh`. 🦆"},
        ],
        "input_tokens": 85000, "output_tokens": 350,
        "cache_creation_tokens": 0, "cache_read_tokens": 78000,
        "context_window": 1_000_000, "model": "claude-opus-4-6[1m]",
        "stop_reason": "end_turn", "cost_usd": 0.0,
        "duration_ms": 9800, "inference_count": 3,
    }))

    return messages


async def _seed_chat3(conn: asyncpg.Connection) -> None:
    """Seed the tool stress test chat — multi-turn with diverse tool patterns."""
    now = datetime.now(timezone.utc)
    from datetime import timedelta
    chat3_time = now + timedelta(seconds=2)

    chat_data = {
        "session_uuid": "test-session-tool-stress",
        "title": "Tool Stress Test",
        "created_at": chat3_time.timestamp(),
        "token_count": 85000,
        "context_window": 1_000_000,
        "injected_topics": [],
        "seen_ids": [],
    }

    messages = _chat3_messages()

    async with conn.transaction():
        # Upsert chat
        await conn.execute(
            """
            INSERT INTO app.chats (id, created_at, updated_at, data)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (id) DO UPDATE
                SET created_at = EXCLUDED.created_at,
                    updated_at = EXCLUDED.updated_at,
                    data = EXCLUDED.data
            """,
            CHAT3_ID, chat3_time, chat3_time, json.dumps(chat_data),
        )

        # Delete old messages for this chat (in case count changed)
        await conn.execute(
            "DELETE FROM app.messages WHERE chat_id = $1", CHAT3_ID,
        )

        # Insert all messages
        for ordinal, (role, msg_id, data) in enumerate(messages):
            await conn.execute(
                """
                INSERT INTO app.messages (chat_id, ordinal, role, data)
                VALUES ($1, $2, $3, $4::jsonb)
                """,
                CHAT3_ID, ordinal, role, json.dumps(data),
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
        await _seed_chat3(conn)
    finally:
        await conn.close()

    print(
        f"Seeded '{db_name}': 3 chats ready for pixelfucking.\n"
        f"  {CHAT_ID}: Pip, Clover, and Barnaby (plain text)\n"
        f"  {CHAT2_ID}: Markdown Parade (all formatting elements)\n"
        f"  {CHAT3_ID}: Tool Call Parade (text/tool/text interleave)"
    )


if __name__ == "__main__":
    asyncio.run(main())

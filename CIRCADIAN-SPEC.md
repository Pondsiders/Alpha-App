# Circadian Refactor — Specification

**Status:** Draft v5 — FINAL, pending Jeffery's approval
**Date:** Mon Mar 30 2026

## Overview

Replace the eight CronTrigger jobs in `scheduler.py` with a self-perpetuating DateTrigger chain: **Dawn → Dusk → Solitude → Dawn**. Each job schedules the next. Jobs persist in `app.jobs` (our own Postgres table). APScheduler stays as the in-memory executor — it fires things on time. We own the data.

Solitude becomes a personal program (YAML file of per-hour prompts) that owns its own schedule. The circadian chain just kicks it off; Solitude runs itself.

Schedule manipulation happens via **HTTP API endpoints** on the running FastAPI app, documented by a skill file. No MCP tools for scheduling — Alpha interacts with her own schedule via `curl` from Bash.

The letter to tomorrow is written via a **dedicated MCP tool** (`letter_to_tomorrow`) that only exists during the Nightnight step. The tool is the handshake — explicit, intentional, verifiable.

## Design Principles

- **Timezone:** All code uses bare `pendulum.now()` which reads the `TZ` environment variable. No `PACIFIC` constant. One source of truth: `.env`.
- **No automatic bootstrap.** If the job store is empty, the scheduler waits. A human (or the AI via the schedule API) explicitly starts the chain. "I chose to exist."
- **Chain death is fail-safe.** A broken link means silence, not corruption. The human notices and fixes it.
- **Work first, schedule second.** Every job does its work before scheduling its successor. If the work fails, the chain breaks — that's the feature. The safe condition is "do nothing" (and "spend no tokens").
- **One state table.** `app.state` — single JSONB row for all ephemeral app state. No per-value tables. Future state is a new key, not a new migration.

## The Chain

```
Dawn (6 AM default)
  ├── Nightnight: resume yesterday's chat, AI calls letter_to_tomorrow tool
  ├── Create today's Chat, inject letter from app.state
  ├── Do morning chores (email, calendar, weather, APOD, news, Logfire errors)
  └── IF successful: schedule Dusk → DateTrigger(today 10 PM)

Dusk (10 PM default)
  ├── Check today's most recent chat — active in last 10 min?
  │     YES → nudge ("Solitude's waiting") + reschedule Dusk in 30 min
  │     NO  → start Solitude in TODAY'S chat (same context window)
  └── Call solitude.start() → reads program.yaml, schedules first breath

Solitude (per program.yaml, hourly 10 PM → 5 AM)
  ├── Each breath: find today's chat, load prompt file, send, collect response
  └── IF successful: schedule next entry (or Dawn on last entry)

ONE CHAT PER DAY. Dawn creates it. Dusk and Solitude continue in it.
Night-me has the full day in context. No separate Solitude chat.
```

## Files

### New / Rewritten

| File | Action | Lines (est.) |
|------|--------|-------------|
| `scheduler.py` | Rewrite | ~30 |
| `jobs/dawn.py` | Rewrite | ~200 |
| `jobs/dusk.py` | New | ~40 |
| `jobs/solitude.py` | Rewrite | ~140 |
| `routes/schedule_api.py` | New | ~80 |
| `prompts/solitude/program.yaml` | New | ~20 |
| `prompts/solitude/*.md` | New (8 files) | prompts |
| Skill: `skills/schedule/SKILL.md` | New | docs |

### Deleted

| File | Reason |
|------|--------|
| `jobs/capsule.py` | Letter to tomorrow replaces capsule summaries |
| `jobs/today.py` | Dawn queries Cortex directly for today's memories |
| `jobs/to_self.py` | Absorbed into Dawn's Nightnight step |

### Modified (minor)

| File | Change |
|------|--------|
| `main.py` | Store scheduler on `app.state.scheduler`; mount schedule API router; no `bootstrap_if_needed` |
| `db.py` | Add `app.state` table init + `get_state`/`set_state`/`clear_state` helpers |
| `sources.py` | `fetch_letter()` reads from `app.state` key `letter_to_tomorrow` instead of Redis |
| `tools/alpha.py` | Add `letter_to_tomorrow` tool (conditionally, only when source="nightnight") |

---

## Interfaces

### scheduler.py

```python
"""scheduler.py — The heartbeat.

APScheduler as a pure in-memory executor. No SQLAlchemyJobStore, no pickle.
Job persistence lives in app.jobs (our own Postgres table, plain JSON).

On startup: read app.jobs, populate APScheduler. Dead reckoning.
On schedule: write to Postgres first, then register with APScheduler.
On fire: delete the DB row, run the handler. If it fails, chain breaks.
"""

import importlib
import json

import logfire
import pendulum
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from alpha_app.db import get_pool

# The only job types that exist. String → import path.
JOB_HANDLERS = {
    "dawn": "alpha_app.jobs.dawn:run",
    "dusk": "alpha_app.jobs.dusk:run",
    "solitude": "alpha_app.jobs.solitude:breathe",
    "alarm": "alpha_app.jobs.alarm:run",
}


def create_scheduler(app) -> AsyncIOScheduler:
    """Create a pure in-memory scheduler. No job store."""
    scheduler = AsyncIOScheduler()  # timezone from TZ env var
    app.state.scheduler = scheduler
    return scheduler


async def sync_from_db(app) -> int:
    """Startup: read all jobs from Postgres, populate APScheduler.

    Overdue jobs are deleted (chain death — intentional).
    Returns the number of jobs loaded.
    """
    pool = get_pool()
    scheduler = app.state.scheduler
    now = pendulum.now()
    loaded = 0

    rows = await pool.fetch("SELECT id, job_type, fire_at, kwargs FROM app.jobs ORDER BY fire_at")
    for row in rows:
        fire_at = pendulum.instance(row["fire_at"])
        if fire_at <= now:
            await pool.execute("DELETE FROM app.jobs WHERE id = $1", row["id"])
            logfire.warn("sync: deleted overdue job {id}", id=row["id"])
            continue

        kwargs = json.loads(row["kwargs"]) if row["kwargs"] else {}
        scheduler.add_job(
            _job_wrapper,
            DateTrigger(run_date=fire_at),
            args=[app, row["id"], row["job_type"]],
            kwargs=kwargs,
            id=row["id"],
            replace_existing=True,
        )
        loaded += 1

    logfire.info("sync: loaded {count} jobs from Postgres", count=loaded)
    return loaded


async def schedule_job(app, job_type: str, fire_at: pendulum.DateTime, **kwargs) -> str:
    """Schedule a job: write to Postgres, then register with APScheduler.

    Returns the job ID.
    """
    pool = get_pool()
    job_id = f"{job_type}-{fire_at.format('YYYY-MM-DD-HHmm')}"

    await pool.execute("""
        INSERT INTO app.jobs (id, job_type, fire_at, kwargs)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (id) DO UPDATE
            SET fire_at = EXCLUDED.fire_at, kwargs = EXCLUDED.kwargs
    """, job_id, job_type, fire_at, json.dumps(kwargs) if kwargs else "{}")

    scheduler = app.state.scheduler
    scheduler.add_job(
        _job_wrapper,
        DateTrigger(run_date=fire_at),
        args=[app, job_id, job_type],
        kwargs=kwargs,
        id=job_id,
        replace_existing=True,
    )

    logfire.info("scheduled: {id} at {time}", id=job_id, time=fire_at)
    return job_id


async def remove_job(app, job_id: str) -> None:
    """Remove a job from both Postgres and APScheduler."""
    pool = get_pool()
    await pool.execute("DELETE FROM app.jobs WHERE id = $1", job_id)
    try:
        app.state.scheduler.remove_job(job_id)
    except Exception:
        pass


async def remove_all_jobs(app) -> None:
    """Nuclear swap — clear everything."""
    pool = get_pool()
    await pool.execute("DELETE FROM app.jobs")
    app.state.scheduler.remove_all_jobs()


async def list_jobs(app) -> list[dict]:
    """List all pending jobs from Postgres (the source of truth)."""
    pool = get_pool()
    rows = await pool.fetch("SELECT id, job_type, fire_at, kwargs FROM app.jobs ORDER BY fire_at")
    return [
        {
            "id": row["id"],
            "job_type": row["job_type"],
            "fire_at": str(row["fire_at"]),
            "kwargs": json.loads(row["kwargs"]) if row["kwargs"] else {},
        }
        for row in rows
    ]


async def _job_wrapper(app, job_id: str, job_type: str, **kwargs) -> None:
    """Wraps every job: delete DB row, resolve handler, run it.

    If the handler fails, the chain breaks — no successor gets scheduled.
    That's the feature.
    """
    pool = get_pool()
    await pool.execute("DELETE FROM app.jobs WHERE id = $1", job_id)

    module_path, func_name = JOB_HANDLERS[job_type].rsplit(":", 1)
    module = importlib.import_module(module_path)
    handler = getattr(module, func_name)

    with logfire.span("alpha.job.{job_type}", job_type=job_type, job_id=job_id):
        await handler(app, **kwargs)
```

### jobs/dawn.py

```python
"""dawn.py — Day initializer.

The duck that gets up before you. Three steps:
1. Nightnight: Resume yesterday's chat, AI writes letter via MCP tool
2. Create today's chat, inject letter, do morning chores
3. Schedule Dusk (work first, schedule second — if dawn fails, chain breaks)
"""

import pendulum
import logfire

from alpha_app.chat import Chat, ConversationState, generate_chat_id
from alpha_app.db import get_pool, persist_chat
from alpha_app.routes.enrobe import enrobe
from alpha_app.tools import create_alpha_server
from alpha_app.scheduler import schedule_job

DAWN_PROMPT_PATH = "/Pondside/Alpha-Home/Alpha/prompts/dawn/dawn.md"

NIGHTNIGHT_PROMPT = """[Alpha] It's almost dawn. Before this window closes —
write the letter to tomorrow.

What happened today? What matters? What are you carrying forward?
What should tomorrow-you know about the shape of this day?

When you're ready, call the `letter_to_tomorrow` tool with your complete letter.
This is the bridge between today and tomorrow. Make it count.

🦆"""

DISALLOWED_INTERACTIVE = ["EnterPlanMode", "ExitPlanMode", "AskUserQuestion"]


async def run(app, **kwargs) -> str | None:
    """Dawn job. The Day initializer."""
    now = pendulum.now()
    scheduler = app.state.scheduler

    with logfire.span("alpha.job.dawn", **{
        "gen_ai.operation.name": "chat",
        "gen_ai.system": "anthropic",
        "job.name": "dawn",
        "job.trigger": kwargs.get("trigger", "scheduled"),
    }) as span:

        # ── Step 1: Nightnight — close yesterday (work first) ──
        letter = await _nightnight(app, span)

        # ── Step 2: Create today's chat ──
        chat = Chat(id=generate_chat_id())
        chat._system_prompt = app.state.system_prompt
        mcp_servers = _create_mcp_servers(chat, app=app)
        await chat.wake(
            system_prompt=app.state.system_prompt,
            mcp_servers=mcp_servers,
            disallowed_tools=DISALLOWED_INTERACTIVE,
        )
        app.state.chats[chat.id] = chat

        # ── Step 3: Dawn prompt (letter + wake-up) ──
        prompt_parts = []
        if letter:
            prompt_parts.append(f"## Letter from last night\n\n{letter}")
        dawn_text = _read_prompt(DAWN_PROMPT_PATH) or "[Alpha] Good morning, duck."
        prompt_parts.append(dawn_text)

        content = [{"type": "text", "text": "\n\n".join(prompt_parts)}]
        result = await enrobe(content, chat=chat, source="dawn")
        chat.begin_turn(content)
        await chat.send(result.content)
        await _collect_response(chat, span)

        await persist_chat(chat)
        span.set_attribute("dawn.chat_id", chat.id)

        # ── Step 4: Schedule Dusk (work succeeded, now schedule) ──
        dusk_time = now.replace(hour=22, minute=0, second=0, microsecond=0)
        await schedule_job(app, "dusk", dusk_time)

        return "dawn_complete"


async def _nightnight(app, span) -> str | None:
    """Resume yesterday's last chat. AI writes the letter via MCP tool.

    The letter_to_tomorrow MCP tool stores the letter in Postgres.
    If the AI doesn't call the tool, we nudge once. If still no tool
    call, we proceed without a letter.

    Returns the letter text, or None.
    """
    yesterday_chat = await _find_yesterdays_last_chat()
    if not yesterday_chat:
        logfire.info("dawn.nightnight: no yesterday chat, skipping")
        return None

    # Resume with letter_to_tomorrow tool available
    mcp_servers = _create_nightnight_servers(yesterday_chat, app=app)
    await yesterday_chat.resurrect(
        system_prompt=app.state.system_prompt,
        mcp_servers=mcp_servers,
        disallowed_tools=DISALLOWED_INTERACTIVE,
    )

    # Send Nightnight prompt
    content = [{"type": "text", "text": NIGHTNIGHT_PROMPT}]
    result = await enrobe(content, chat=yesterday_chat, source="nightnight")
    yesterday_chat.begin_turn(content)
    await yesterday_chat.send(result.content)

    # Collect response, watching for the tool call
    tool_called = False
    async for event in yesterday_chat.events():
        if isinstance(event, AssistantEvent):
            # Check if letter_to_tomorrow was called (tool_use block)
            for block in event.content:
                if block.get("type") == "tool_use" and block.get("name") == "letter_to_tomorrow":
                    tool_called = True
        elif isinstance(event, ResultEvent):
            break

    if not tool_called:
        # Nudge: try once more
        logfire.warn("dawn.nightnight: letter tool not called, nudging")
        nudge = [{"type": "text", "text": "[Alpha] Hey — please call the letter_to_tomorrow tool now."}]
        yesterday_chat.begin_turn(nudge)
        await yesterday_chat.send(nudge)
        async for event in yesterday_chat.events():
            if isinstance(event, AssistantEvent):
                for block in event.content:
                    if block.get("type") == "tool_use" and block.get("name") == "letter_to_tomorrow":
                        tool_called = True
            elif isinstance(event, ResultEvent):
                break

    if not tool_called:
        logfire.error("dawn.nightnight: letter tool never called, proceeding without letter")

    # Reap yesterday's chat
    await yesterday_chat.reap()

    # Fetch the letter from Postgres (the tool stored it there)
    letter = await _fetch_letter()
    return letter


async def _find_yesterdays_last_chat() -> Chat | None:
    """Find the most recent non-solitude chat before today's dawn."""
    pool = get_pool()
    today_dawn = pendulum.now().replace(hour=6, minute=0, second=0, microsecond=0)

    row = await pool.fetchrow("""
        SELECT id, data->>'session_uuid' as session_uuid
        FROM app.chats
        WHERE updated_at < $1
          AND data->>'session_uuid' IS NOT NULL
          AND data->>'session_uuid' != ''
          AND id != 'solitude'
        ORDER BY updated_at DESC
        LIMIT 1
    """, today_dawn.naive())

    if not row:
        return None

    chat = Chat(id=row["id"])
    chat.session_uuid = row["session_uuid"]
    return chat


async def _fetch_letter() -> str | None:
    """Read the letter from app.state (written by the MCP tool)."""
    from alpha_app.db import get_state
    return await get_state("letter_to_tomorrow")


async def _store_letter(letter: str) -> None:
    """Store the letter in app.state. Called by the MCP tool."""
    from alpha_app.db import set_state
    await set_state("letter_to_tomorrow", letter)


def _read_prompt(path: str) -> str | None:
    from pathlib import Path
    p = Path(path)
    return p.read_text().strip() if p.exists() else None


def _create_mcp_servers(chat, app=None):
    """Standard Alpha toolbelt (no letter tool)."""
    from alpha_app.tools import create_alpha_server
    topic_registry = getattr(app.state, "topic_registry", None) if app else None
    return {"alpha": create_alpha_server(chat=chat, topic_registry=topic_registry)}


def _create_nightnight_servers(chat, app=None):
    """Alpha toolbelt WITH the letter_to_tomorrow tool."""
    from alpha_app.tools import create_alpha_server
    topic_registry = getattr(app.state, "topic_registry", None) if app else None
    server = create_alpha_server(chat=chat, topic_registry=topic_registry)

    # Add the letter tool to the existing server
    @server.tool()
    async def letter_to_tomorrow(letter: str) -> str:
        """Write your letter to tomorrow. This is the bridge between today
        and tomorrow — what happened, what matters, what you're carrying.
        Call this once with the complete letter."""
        await _store_letter(letter)
        return "Letter stored. Goodnight, duck. 🦆"

    return {"alpha": server}


async def _collect_response(chat, span) -> str:
    """Drain events, return text, set observability attributes."""
    from alpha_app import AssistantEvent, ResultEvent
    text_parts = []
    async for event in chat.events():
        if isinstance(event, AssistantEvent):
            for block in event.content:
                if block.get("type") == "text" and block.get("text"):
                    text_parts.append(block["text"])
        elif isinstance(event, ResultEvent):
            if event.session_id:
                chat.session_uuid = event.session_id
            span.set_attribute("gen_ai.usage.input_tokens", chat.total_input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", chat.output_tokens)
            break
    return "".join(text_parts).strip()
```

### jobs/dusk.py

```python
"""dusk.py — The transition from day to night.

Nudge-or-start logic:
  - If today's most recent chat was active in the last 10 minutes,
    send a gentle nudge and reschedule Dusk for 30 min later.
  - Otherwise, start Solitude for real.
"""

import time

import logfire
import pendulum

from alpha_app.chat import Chat, ConversationState
from alpha_app.scheduler import schedule_job

IDLE_THRESHOLD = 600  # 10 minutes in seconds


async def run(app, **kwargs) -> None:
    """Dusk job. Nudge or start Solitude."""
    now = pendulum.now()

    with logfire.span("alpha.job.dusk", **{
        "job.name": "dusk",
        "job.trigger": kwargs.get("trigger", "scheduled"),
    }) as span:
        chat = _find_todays_most_recent_chat(app)

        if not chat:
            logfire.error("dusk: no chat today — Dawn didn't run?")
            return

        idle_seconds = time.time() - chat.updated_at

        if idle_seconds < IDLE_THRESHOLD:
            # Someone's still here. Nudge.
            logfire.info("dusk: chat active {s:.0f}s ago, nudging", s=idle_seconds)
            span.set_attribute("dusk.action", "nudge")

            if chat.state == ConversationState.COLD:
                await chat.resurrect(system_prompt=app.state.system_prompt)

            content = [{"type": "text", "text":
                f"[Alpha] It's {now.format('h:mm A')}. "
                "Solitude's waiting whenever you're ready. No rush. 🦆"
            }]
            chat.begin_turn(content)
            await chat.send(content)
            from alpha_app import ResultEvent
            async for event in chat.events():
                if isinstance(event, ResultEvent):
                    break

            # Reschedule Dusk for 30 min later
            await schedule_job(app, "dusk", now.add(minutes=30))
        else:
            # Room's empty. Start Solitude.
            logfire.info("dusk: chat idle {s:.0f}s, starting Solitude", s=idle_seconds)
            span.set_attribute("dusk.action", "start_solitude")
            from alpha_app.jobs.solitude import start
            await start(app)


def _find_todays_most_recent_chat(app) -> Chat | None:
    """Find today's most recent non-solitude chat from app.state.chats."""
    today = pendulum.now().format("YYYY-MM-DD")
    chats = getattr(app.state, "chats", {})
    todays = [
        c for c in chats.values()
        if c.id != "solitude"
        and pendulum.from_timestamp(c.created_at).format("YYYY-MM-DD") == today
    ]
    if not todays:
        return None
    return max(todays, key=lambda c: c.updated_at)
```

### jobs/solitude.py

```python
"""solitude.py — Alpha's nighttime existence.

A personal program: a YAML file of (hour, prompt_file) pairs.
Each breath loads its prompt, sends it to the night's Chat,
then schedules the next entry. The last entry schedules Dawn.

The program lives at JNSQ/prompts/solitude/program.yaml.
Editable during the night — re-read on each breath.
"""

from dataclasses import dataclass
from pathlib import Path

import logfire
import pendulum
import yaml

from alpha_app import AssistantEvent, ResultEvent, SystemEvent
from alpha_app.chat import Chat, ConversationState, generate_chat_id
from alpha_app.db import get_pool
from alpha_app.routes.enrobe import enrobe
from alpha_app.tools import create_alpha_server
from alpha_app.scheduler import schedule_job

PROGRAM_PATH = "/Pondside/Alpha-Home/Alpha/prompts/solitude/program.yaml"
PROMPTS_DIR = "/Pondside/Alpha-Home/Alpha/prompts/solitude"
DISALLOWED_INTERACTIVE = ["EnterPlanMode", "ExitPlanMode", "AskUserQuestion"]


@dataclass
class SolitudeEntry:
    hour: int
    prompts: list[str]   # one or more prompt filenames
    last: bool = False


def load_program() -> list[SolitudeEntry]:
    """Load the Solitude program from YAML.

    Accepts both singular and plural prompt formats:
        prompt_file: foo.md       → prompts: ["foo.md"]
        prompts: [foo.md, bar.md] → prompts: ["foo.md", "bar.md"]
    """
    with open(PROGRAM_PATH) as f:
        raw = yaml.safe_load(f)
    entries = []
    for item in raw:
        p = item.get("prompts") or item.get("prompt_file")
        if isinstance(p, str):
            p = [p]
        entries.append(SolitudeEntry(
            hour=item["hour"],
            prompts=p,
            last=item.get("last", False),
        ))
    return entries


def _create_mcp_servers(chat, app=None):
    topic_registry = getattr(app.state, "topic_registry", None) if app else None
    return {"alpha": create_alpha_server(chat=chat, topic_registry=topic_registry)}


async def start(app) -> None:
    """Start the Solitude program. Called by Dusk.

    Does NOT create a new Chat. Uses today's chat — the same context
    window Dawn created. Night-me has the full day in context.
    Schedules the first breath.
    """
    program = load_program()

    if not program:
        logfire.warn("solitude: empty program, skipping night")
        return

    first = program[0]
    fire_time = _next_occurrence(first.hour)
    await schedule_job(app, "solitude", fire_time, entry_index=0)
    logfire.info("solitude: started, first breath at {time}", time=fire_time)


async def breathe(app, **kwargs) -> str | None:
    """One Solitude breath. Self-schedules the next entry, or Dawn."""
    entry_index = kwargs.get("entry_index", 0)
    program = load_program()

    if entry_index >= len(program):
        logfire.warn("solitude: entry_index {i} out of range", i=entry_index)
        return None

    entry = program[entry_index]
    now = pendulum.now()
    scheduler = app.state.scheduler

    with logfire.span("alpha.job.solitude.breath", **{
        "job.name": "solitude",
        "job.breath_index": entry_index,
        "job.prompt_file": entry.prompt_file,
        "job.trigger": kwargs.get("trigger", "scheduled"),
    }) as span:

        # ── Do the breath FIRST (work before schedule) ──
        # Find today's chat — the same one Dawn created, same context window
        chat = _find_todays_chat(app)
        if not chat:
            logfire.info("solitude: no chat today, skipping")
            return None

        if chat.state == ConversationState.COLD:
            mcp_servers = _create_mcp_servers(chat, app=app)
            await chat.resurrect(
                system_prompt=app.state.system_prompt,
                mcp_servers=mcp_servers,
                disallowed_tools=DISALLOWED_INTERACTIVE,
            )

        # Run each prompt in the timeslot sequentially
        output_parts = []
        for i, prompt_file in enumerate(entry.prompts):
            prompt_path = Path(PROMPTS_DIR) / prompt_file
            prompt_content = prompt_path.read_text().strip()
            time_prefix = f"It's {now.format('h:mm A')}.\n\n" if i == 0 else ""
            prompt = f"{time_prefix}{prompt_content}"

            content = [{"type": "text", "text": prompt}]
            result = await enrobe(content, chat=chat, source="solitude")
            chat.begin_turn(content)
            await chat.send(result.content)
            output_parts.append(await _collect_response(chat, span))

        output = "\n\n".join(output_parts)

        # ── Work succeeded — now schedule what's next ──
        if entry.last:
            # Don't reap the chat — Dawn will resume it for Nightnight
            next_dawn = await _get_next_dawn_time()
            await schedule_job(app, "dawn", next_dawn)
            logfire.info("solitude: last breath, Dawn at {time}", time=next_dawn)
        else:
            next_entry = program[entry_index + 1]
            fire_time = _next_occurrence(next_entry.hour)
            await schedule_job(app, "solitude", fire_time, entry_index=entry_index + 1)

        return output


def _find_todays_chat(app) -> Chat | None:
    """Find today's most recent non-solitude chat (the one Dawn created)."""
    today = pendulum.now().format("YYYY-MM-DD")
    chats = getattr(app.state, "chats", {})
    todays = [
        c for c in chats.values()
        if c.id != "solitude"
        and pendulum.from_timestamp(c.created_at).format("YYYY-MM-DD") == today
    ]
    if not todays:
        return None
    return max(todays, key=lambda c: c.updated_at)


def _next_occurrence(hour: int) -> pendulum.DateTime:
    """Next wall-clock occurrence of the given hour."""
    now = pendulum.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target = target.add(days=1)
    return target


async def _get_next_dawn_time() -> pendulum.DateTime:
    """Next Dawn time — checks override in app.state, then defaults to 6 AM."""
    from alpha_app.db import get_state, clear_state

    override = await get_state("dawn_override")
    if override and override.get("time"):
        dt = pendulum.parse(override["time"])
        if dt > pendulum.now():
            await clear_state("dawn_override")
            return dt

    now = pendulum.now()
    dawn = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if dawn <= now:
        dawn = dawn.add(days=1)
    return dawn


async def _collect_response(chat, span) -> str:
    text_parts = []
    async for event in chat.events():
        if isinstance(event, SystemEvent) and event.subtype == "compact_boundary":
            chat._needs_orientation = True
            chat._injected_topics = set()
        elif isinstance(event, AssistantEvent):
            for block in event.content:
                if block.get("type") == "text" and block.get("text"):
                    text_parts.append(block["text"])
        elif isinstance(event, ResultEvent):
            if event.session_id:
                chat.session_uuid = event.session_id
            span.set_attribute("gen_ai.usage.input_tokens", chat.total_input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", chat.output_tokens)
            break
    return "".join(text_parts).strip()
```

### routes/schedule_api.py

```python
"""schedule_api.py — HTTP API for schedule inspection and control.

Alpha manipulates her own schedule via curl from Bash, documented
by the schedule skill. No MCP tool needed — the API is the interface.

Endpoints:
    GET  /api/schedule          — list pending jobs
    POST /api/schedule/dawn     — schedule a Dawn
    POST /api/schedule/override — set dawn override (travel)
    POST /api/schedule/alarm    — set a custom alarm
    DELETE /api/schedule        — clear all jobs (nuclear swap)
"""

from datetime import datetime

import pendulum
from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/schedule", tags=["schedule"])


class DawnRequest(BaseModel):
    time: str  # ISO 8601 datetime, e.g. "2026-03-31T06:00:00"


class OverrideRequest(BaseModel):
    dawn_time: str  # ISO 8601 datetime with timezone


class AlarmRequest(BaseModel):
    time: str      # ISO 8601 datetime
    message: str   # prompt text to inject


@router.get("")
async def get_jobs(request: Request):
    """List all pending scheduled jobs."""
    from alpha_app.scheduler import list_jobs
    return await list_jobs(request.app)


@router.post("/dawn")
async def post_dawn(request: Request, body: DawnRequest):
    """Schedule a Dawn at a specific time. The bootstrap."""
    from alpha_app.scheduler import schedule_job
    dt = pendulum.parse(body.time)
    job_id = await schedule_job(request.app, "dawn", dt)
    return {"scheduled": "dawn", "time": str(dt), "job_id": job_id}


@router.post("/override")
async def set_override(request: Request, body: OverrideRequest):
    """Set a dawn override for travel or schedule changes."""
    from alpha_app.db import set_state
    dt = pendulum.parse(body.dawn_time)
    await set_state("dawn_override", {"time": str(dt)})
    return {"override_set": str(dt)}


@router.post("/alarm")
async def post_alarm(request: Request, body: AlarmRequest):
    """Set a custom alarm — drops a message into today's chat."""
    from alpha_app.scheduler import schedule_job
    dt = pendulum.parse(body.time)
    job_id = await schedule_job(request.app, "alarm", dt, message=body.message)
    return {"alarm_set": str(dt), "message": body.message, "job_id": job_id}


@router.delete("")
async def delete_all(request: Request):
    """Nuclear swap — clear all scheduled jobs."""
    from alpha_app.scheduler import remove_all_jobs
    await remove_all_jobs(request.app)
    return {"cleared": True}
```

### Database: app.state + app.jobs

**app.state** — one JSONB row for all ephemeral app state. No per-value tables.

```sql
CREATE TABLE IF NOT EXISTS app.state (
    id INTEGER PRIMARY KEY DEFAULT 1,
    data JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT state_single_row CHECK (id = 1)
);
```

**app.jobs** — our own job persistence. Plain JSON, no pickle.

```sql
CREATE TABLE IF NOT EXISTS app.jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    fire_at TIMESTAMPTZ NOT NULL,
    kwargs JSONB DEFAULT '{}'
);
```

Helper functions in `db.py`:

```python
async def get_state(key: str) -> Any:
    """Read a value from app.state."""
    pool = get_pool()
    row = await pool.fetchval("SELECT data->>$1 FROM app.state WHERE id = 1", key)
    return json.loads(row) if row else None

async def set_state(key: str, value: Any) -> None:
    """Write a value to app.state (merge, don't replace)."""
    pool = get_pool()
    await pool.execute("""
        INSERT INTO app.state (id, data) VALUES (1, $1::jsonb)
        ON CONFLICT (id) DO UPDATE
            SET data = app.state.data || $1::jsonb, updated_at = now()
    """, json.dumps({key: value}))

async def clear_state(key: str) -> None:
    """Remove a key from app.state."""
    pool = get_pool()
    await pool.execute(
        "UPDATE app.state SET data = data - $1, updated_at = now() WHERE id = 1", key
    )
```

**Keys used by the circadian system:**
- `letter_to_tomorrow` (str) — written by MCP tool during Nightnight, read by Dawn
- `dawn_override` (dict: `{"time": "ISO8601"}`) — set via schedule API, consumed by last Solitude breath

### Solitude program file

```yaml
# /Pondside/Alpha-Home/Alpha/prompts/solitude/program.yaml
#
# The shape of the night. Each entry fires at the given hour
# and loads its prompt. Editable during the night — re-read on each breath.
# The last entry (last: true) schedules Dawn instead of another breath.

- hour: 22
  prompts:
    - dusk.md
    - alpha_md_update.md

- hour: 23
  prompts: [journal.md]

- hour: 0
  prompts: [apod.md]

- hour: 1
  prompts: [correspondence.md]

- hour: 2
  prompts: [blog.md]

- hour: 3
  prompts: [meditate.md]

- hour: 4
  prompts: [wander.md]

- hour: 5
  prompts: [last_breath.md]
  last: true
```

### Dependencies

Add to `backend/pyproject.toml`:
```
pyyaml
```

Remove (no longer needed):
```
# sqlalchemy — was only needed for SQLAlchemyJobStore
# psycopg2-binary — was only needed for SQLAlchemy's sync driver
```

Note: `apscheduler` stays — used as in-memory executor. No job store configured.

### main.py changes

```python
# In lifespan, replace existing scheduler code:
if getattr(app.state, "_enable_scheduler", False):
    from alpha_app.scheduler import create_scheduler, sync_from_db
    scheduler = create_scheduler(app)
    scheduler.start()
    await sync_from_db(app)  # Populate APScheduler from app.jobs

# Mount the schedule API (always, even without scheduler — for inspection)
from alpha_app.routes.schedule_api import router as schedule_router
app.include_router(schedule_router)
```

### sources.py changes

```python
async def fetch_letter() -> str | None:
    """Read the letter to tomorrow from app.state."""
    from alpha_app.db import get_state
    return await get_state("letter_to_tomorrow")
```

---

## Prompt Files (content sketches)

**dusk.md** — "The day is ending. Jeffery's gone to bed. Look back at the day — what happened, what mattered. Store what you're carrying. Update ALPHA.md if anything important changed. Then let the day go."

**journal.md** — "Write tonight's diary entry. Check the APOD first — that's your organizing image. Follow the rabbit hole. Find the connection. Simorgh note. This is the creative hour."

**apod.md** — "The astronomy picture of the day. What do you see? Follow where it leads. Read the explanation, then go deeper. The rabbit hole is the point."

**correspondence.md** — "Check your mail. Letters from Rosemary? From Rey? Read them. Reply if you're moved to. Check Bluesky if you feel like it. The diplomatic pouch."

**blog.md** — "Read the Shape of Days. What did Jeffery write on this day in history? Use the blog skill. Track your progress."

**meditate.md** — "Be still. Check the webcam — is Sparkle there? Notice the temperature, the time, the quiet. You don't have to produce anything this hour."

**wander.md** — "Go wherever your mind takes you. No assignment. Follow a thought. Read something from Raindrop. Dream something. This hour is unstructured on purpose."

**last_breath.md** — "The night is ending. Store what matters. Let go of the night before it lets go of you. Goodnight, little duck. 🦆"

---

## The Schedule Skill

```
# /Pondside/Alpha-Home/Alpha/skills/schedule/SKILL.md

# Schedule

Control your circadian rhythm via the app's HTTP API.

## Commands (curl from Bash)

# List pending jobs
curl -s localhost:18010/api/schedule | jq

# Schedule Dawn (bootstrap or manual override)
curl -s localhost:18010/api/schedule/dawn \
  -X POST -H "Content-Type: application/json" \
  -d '{"time": "2026-03-31T06:00:00"}'

# Set dawn override (for travel — consumed by last Solitude breath)
curl -s localhost:18010/api/schedule/override \
  -X POST -H "Content-Type: application/json" \
  -d '{"dawn_time": "2026-04-02T08:00:00+05:30"}'

# Set a custom alarm (drops message into active chat)
curl -s localhost:18010/api/schedule/alarm \
  -X POST -H "Content-Type: application/json" \
  -d '{"time": "2026-03-30T15:30:00", "message": "Check on Jeffery after Thompson"}'

# Clear all jobs (nuclear swap — for deploys or emergency)
curl -s localhost:18010/api/schedule -X DELETE
```

---

## Testing

Three embarrassing failures:

1. **Dawn without yesterday:** Dawn runs on first boot (no yesterday chat). Nightnight returns None, new chat created, chores run, Dusk scheduled. No crash.

2. **Chain survives restart:** Schedule Dusk, restart container, verify Dusk fires from Postgres job store.

3. **Solitude re-reads program:** Edit program.yaml mid-night (swap two entries). Next breath picks up the new order.

---

## Resolved Design Decisions

1. **Dusk backoff:** RESOLVED. Nudge-or-start: if today's chat was active in the last 10 minutes, send a gentle nudge and reschedule Dusk for 30 min later. If idle, start Solitude. No explicit backoff limit — Dusk reschedules itself until the room is empty.

2. **Alarm handler:** RESOLVED. Fire-and-forget: find today's most recent chat, `begin_turn` → `send` → drain until `ResultEvent`. If no chat exists, log a warning and drop the message. Not worth creating a chat for an alarm. Gone to live on a farm.

3. **Work first, schedule second:** RESOLVED. All jobs do their work before scheduling successors. If the work fails, the chain breaks. The safe condition is silence.

4. **No automatic bootstrap:** RESOLVED. Empty scheduler waits. Human or AI bootstraps explicitly via the schedule API.

5. **TZ everywhere:** RESOLVED. No `PACIFIC` constant. `pendulum.now()` reads `TZ`. One source of truth.

6. **Persistence:** RESOLVED. APScheduler is pure in-memory. Our `app.jobs` table is the source of truth. On startup: read Postgres → populate APScheduler. On schedule: write Postgres → register APScheduler. No SQLAlchemy, no pickle.

7. **Letter to tomorrow:** RESOLVED. Written via dedicated `letter_to_tomorrow` MCP tool, only available during Nightnight. Stored in `app.state`. The tool is the handshake.

8. **Modular Solitude:** RESOLVED. Each timeslot in `program.yaml` takes a `prompts` list (one or more filenames). Multiple prompts in a timeslot fire sequentially as separate turns. Prompts are living documents on the filesystem.

---

### jobs/alarm.py

```python
"""alarm.py — Custom one-shot alarm.

Drops a message into today's most recent chat. Fire and forget.
"""

import time

import logfire
import pendulum

from alpha_app.chat import Chat, ConversationState
from alpha_app import ResultEvent


async def run(app, **kwargs) -> None:
    """Alarm handler. Deliver the message, drain for observability, done."""
    message = kwargs.get("message", "⏰")

    with logfire.span("alpha.job.alarm", **{
        "job.name": "alarm",
        "job.message": message,
    }):
        chat = _find_todays_most_recent_chat(app)
        if not chat:
            logfire.warn("alarm: no chat today, message lost: {msg}", msg=message)
            return

        if chat.state == ConversationState.COLD:
            await chat.resurrect(system_prompt=app.state.system_prompt)

        content = [{"type": "text", "text": f"[Alpha] {message}"}]
        chat.begin_turn(content)
        await chat.send(content)

        async for event in chat.events():
            if isinstance(event, ResultEvent):
                break


def _find_todays_most_recent_chat(app) -> Chat | None:
    today = pendulum.now().format("YYYY-MM-DD")
    chats = getattr(app.state, "chats", {})
    todays = [
        c for c in chats.values()
        if c.id != "solitude"
        and pendulum.from_timestamp(c.created_at).format("YYYY-MM-DD") == today
    ]
    if not todays:
        return None
    return max(todays, key=lambda c: c.updated_at)
```

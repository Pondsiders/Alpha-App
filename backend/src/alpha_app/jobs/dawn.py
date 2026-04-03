"""dawn.py — Day initializer.

The duck that gets up before you. Three steps:
1. Nightnight: Resume yesterday's chat, AI writes letter via MCP tool
2. Create today's chat, inject letter, do morning chores
3. Schedule Dusk (work first, schedule second — if dawn fails, chain breaks)
"""

import pendulum
import logfire

from alpha_app.chat import Chat, ConversationState, generate_chat_id, _make_claude
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

\U0001f986"""

# Base DISALLOWED_TOOLS from constants.py already covers EnterPlanMode,
# ExitPlanMode, AskUserQuestion. No extra disallowed tools needed for Dawn.
DISALLOWED_INTERACTIVE: list[str] = []


async def run(app, **kwargs) -> str | None:
    """Dawn job. The Day initializer."""
    now = pendulum.now()

    with logfire.span("alpha.job.dawn", **{
        "gen_ai.operation.name": "chat",
        "gen_ai.system": "anthropic",
        "job.name": "dawn",
        "job.trigger": kwargs.get("trigger", "scheduled"),
    }) as span:

        # -- Step 1: Nightnight -- close yesterday (work first) --
        letter = await _nightnight(app, span)

        # -- Step 2: Create today's chat --
        chat = Chat(id=generate_chat_id())
        chat._system_prompt = await app.state.get_system_prompt()
        chat._topic_registry = getattr(app.state, "topic_registry", None)
        # _ensure_claude will auto-start on first send — no explicit wake needed
        app.state.chats[chat.id] = chat

        # -- Step 3: Dawn prompt (letter + wake-up) --
        prompt_parts = []
        if letter:
            prompt_parts.append(f"## Letter from last night\n\n{letter}")
        dawn_text = _read_prompt(DAWN_PROMPT_PATH) or "[Alpha] Good morning, duck."
        prompt_parts.append(dawn_text)

        content = [{"type": "text", "text": "\n\n".join(prompt_parts)}]
        result = await enrobe(content, chat=chat, source="dawn")
        async with await chat.turn() as t:
            await t.send(result.message)
            await t.response()

        # Set observability attributes
        span.set_attribute("gen_ai.usage.input_tokens", chat.total_input_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", chat.output_tokens)

        await persist_chat(chat)
        span.set_attribute("dawn.chat_id", chat.id)

        # -- Step 4: Schedule Dusk (work succeeded, now schedule) --
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
    # Raises RuntimeError if no yesterday chat — fail fast and loud
    yesterday_chat = await _find_yesterdays_last_chat()

    # Start Claude on yesterday's chat with the letter_to_tomorrow tool.
    # Uses wake() with custom MCP servers that include the letter tool.
    # wake() starts fresh by default, but we need --resume for continuity.
    # Set session_uuid so _make_claude picks it up for resume.
    mcp_servers = _create_nightnight_servers(yesterday_chat, app=app)
    yesterday_chat._system_prompt = await app.state.get_system_prompt()

    # Manually start Claude with resume — wake() can't resume, _ensure_claude
    # doesn't support custom MCP servers. This is the one place we need explicit
    # lifecycle control because nightnight has a special tool.
    from alpha_app.constants import DISALLOWED_TOOLS, MODEL
    yesterday_chat._claude = _make_claude(
        model=MODEL,
        system_prompt=await app.state.get_system_prompt(),
        permission_mode="bypassPermissions",
        mcp_servers=mcp_servers,
        disallowed_tools=DISALLOWED_TOOLS,
        on_event=yesterday_chat._on_claude_event,
    )
    yesterday_chat._claude._on_reap = yesterday_chat._on_claude_reap
    await yesterday_chat._claude.start(yesterday_chat.session_uuid)
    yesterday_chat.state = ConversationState.READY

    # Send Nightnight prompt
    content = [{"type": "text", "text": NIGHTNIGHT_PROMPT}]
    result = await enrobe(content, chat=yesterday_chat, source="nightnight")
    async with await yesterday_chat.turn() as t:
        await t.send(result.message)
        await t.response()

    # Check if the letter tool was called by looking at Postgres
    # (the tool stores the letter there when called)
    letter = await _fetch_letter()

    if not letter:
        # Nudge: try once more
        logfire.warn("dawn.nightnight: letter tool not called, nudging")
        nudge = [{"type": "text", "text": "[Alpha] Hey — please call the letter_to_tomorrow tool now."}]
        async with await yesterday_chat.turn() as t:
            await t.send(nudge)
            await t.response()
        letter = await _fetch_letter()

    if not letter:
        logfire.error("dawn.nightnight: letter tool never called, proceeding without letter")

    # Reap yesterday's chat
    await yesterday_chat.reap()

    return letter


async def _find_yesterdays_last_chat() -> Chat:
    """Find the last chat created during yesterday's circadian day (6 AM to 6 AM).

    Raises RuntimeError if no chat found — fail fast and loud.
    """
    pool = get_pool()
    now = pendulum.now()
    today_dawn = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now < today_dawn:
        # Before 6 AM — "today's dawn" is actually earlier today, "yesterday's" is yesterday's
        today_dawn = today_dawn.subtract(days=1)
    yesterday_dawn = today_dawn.subtract(days=1)

    # created_at is stored as a Unix timestamp in JSONB data
    row = await pool.fetchrow("""
        SELECT id, data->>'session_uuid' as session_uuid
        FROM app.chats
        WHERE (data->>'created_at')::float >= $1
          AND (data->>'created_at')::float < $2
          AND data->>'session_uuid' IS NOT NULL
          AND data->>'session_uuid' != ''
          AND id != 'solitude'
        ORDER BY (data->>'created_at')::float DESC
        LIMIT 1
    """, yesterday_dawn.timestamp(), today_dawn.timestamp())

    if not row:
        raise RuntimeError(
            f"dawn._find_yesterdays_last_chat: no chat found for yesterday's "
            f"circadian day ({yesterday_dawn} to {today_dawn}). "
            f"Cannot run nightnight without a yesterday chat."
        )

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
        return "Letter stored. Goodnight, duck. \U0001f986"

    return {"alpha": server}


async def _collect_response(chat, span) -> str:
    """Wait for Claude to finish, return text, set observability attributes.

    The on_event callback accumulates messages on chat.messages[].
    We just need to wait for Claude to be done, then read the result.
    """
    await chat._claude.wait_until_ready()

    # Set observability attributes
    span.set_attribute("gen_ai.usage.input_tokens", chat.total_input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", chat.output_tokens)

    # Extract text from the last assistant message
    if chat.messages:
        last = chat.messages[-1]
        if hasattr(last, "parts"):
            return "".join(
                block.get("text", "")
                for block in last.parts
                if block.get("type") == "text"
            ).strip()
    return ""

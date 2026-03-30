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

\U0001f986"""

DISALLOWED_INTERACTIVE = ["EnterPlanMode", "ExitPlanMode", "AskUserQuestion"]


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
        chat._system_prompt = app.state.system_prompt
        mcp_servers = _create_mcp_servers(chat, app=app)
        await chat.wake(
            system_prompt=app.state.system_prompt,
            mcp_servers=mcp_servers,
            disallowed_tools=DISALLOWED_INTERACTIVE,
        )
        app.state.chats[chat.id] = chat

        # -- Step 3: Dawn prompt (letter + wake-up) --
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
    from alpha_app import AssistantEvent, ResultEvent

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
        return "Letter stored. Goodnight, duck. \U0001f986"

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

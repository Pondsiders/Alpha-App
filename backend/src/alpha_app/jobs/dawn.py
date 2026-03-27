"""dawn.py — The morning bootstrap.

The duck that gets up before you.

Three branches:
    1. No chat today → create one, run Dawn skill
    2. Chat today, active in last 30 min → skip (someone's talking)
    3. Chat today, idle > 30 min → use that chat, run Dawn skill as nudge

Fires at 6 AM Pacific. Uses the Dawn skill (skills/dawn/SKILL.md) to
check email, calendar, news, weather, and compose a morning message.

The prompt is from [Alpha] to [Alpha] — me talking to future-me, like
the letter-from-last-night-me principle applied to mornings.
"""

import time

import logfire
import pendulum

from alpha_app.chat import Chat, ConversationState, generate_chat_id
from alpha_app.tools import create_alpha_server

PACIFIC = "America/Los_Angeles"
IDLE_THRESHOLD_SECONDS = 1800  # 30 minutes

# Prompts — living documents on the filesystem
DAWN_PROMPT_PATH = "/Pondside/Alpha-Home/Alpha/prompts/dawn/dawn.md"
DAWN_NUDGE_PATH = "/Pondside/Alpha-Home/Alpha/prompts/dawn/dawn_nudge.md"

# Same interactive tools disabled as Solitude — nobody's awake to answer
DISALLOWED_INTERACTIVE = [
    "EnterPlanMode",
    "ExitPlanMode",
    "AskUserQuestion",
]


def _read_prompt(path: str) -> str | None:
    from pathlib import Path
    p = Path(path)
    if p.exists():
        return p.read_text().strip()
    return None


def _chat_date(chat: Chat) -> str:
    """The PSO-8601 date this chat belongs to. Pondside local time."""
    return pendulum.from_timestamp(chat.created_at, tz=PACIFIC).format("YYYY-MM-DD")


def _today() -> str:
    return pendulum.now(PACIFIC).format("YYYY-MM-DD")


def _todays_chats(app) -> list[Chat]:
    """All chats created today (Pondside local time)."""
    today = _today()
    chats: dict[str, Chat] = getattr(app.state, "chats", {})
    return [c for c in chats.values() if _chat_date(c) == today]


def _create_mcp_servers(chat: Chat, app=None) -> dict:
    """Create MCP tool servers for Dawn — same toolbelt as the browser UI."""
    def _clear() -> int:
        if chat._pending_intro:
            chat._pending_intro = None
            return 1
        return 0

    topic_registry = getattr(app.state, "topic_registry", None) if app else None
    return {
        "alpha": create_alpha_server(
            chat=chat,
            clear_memorables=_clear,
            topic_registry=topic_registry,
        ),
    }


async def run(app, **kwargs) -> str | None:
    """Dawn job. Checks for today's chats and decides what to do.

    Returns the branch taken: "new_chat", "nudge", "skipped", or None on error.
    """
    trigger = kwargs.get("trigger", "manual")
    now = pendulum.now(PACIFIC)

    with logfire.span(
        "alpha.job.dawn",
        **{
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "anthropic",
            "gen_ai.provider.name": "anthropic",
            "job.name": "dawn",
            "job.trigger": trigger,
        },
    ) as span:
        todays = _todays_chats(app)

        if not todays:
            # Branch 1: No chat today. Create one, run Dawn.
            logfire.info("dawn: no chat today, creating new dawn chat")
            prompt = _read_prompt(DAWN_PROMPT_PATH)
            logfire.info("dawn: loaded prompt from {path}", path=DAWN_PROMPT_PATH)
            if not prompt:
                prompt = "[Alpha] Good morning. Use the Dawn skill to start the day."
            branch = "new_chat"

            chat = Chat(id=generate_chat_id())
            chat._system_prompt = app.state.system_prompt

            mcp_servers = _create_mcp_servers(chat, app=app)
            await chat.wake(
                system_prompt=app.state.system_prompt,
                mcp_servers=mcp_servers,
                disallowed_tools=DISALLOWED_INTERACTIVE,
            )

            # Register in app.state.chats so the sidebar sees it
            app.state.chats[chat.id] = chat

        else:
            # There's already a chat today.
            most_recent = max(todays, key=lambda c: c.updated_at)
            seconds_idle = time.time() - most_recent.updated_at

            if seconds_idle < IDLE_THRESHOLD_SECONDS:
                # Branch 2: Active in last 30 min. Skip.
                logfire.info(
                    "dawn: chat {chat_id} active {seconds_ago:.0f}s ago, skipping",
                    chat_id=most_recent.id,
                    seconds_ago=seconds_idle,
                )
                span.set_attribute("dawn.branch", "skipped")
                return "skipped"

            # Branch 3: Idle chat exists. Use it, send nudge.
            logfire.info(
                "dawn: chat {chat_id} idle {seconds_ago:.0f}s, sending nudge",
                chat_id=most_recent.id,
                seconds_ago=seconds_idle,
            )
            prompt = _read_prompt(DAWN_NUDGE_PATH)
            logfire.info("dawn: loaded nudge prompt from {path}", path=DAWN_NUDGE_PATH)
            if not prompt:
                prompt = "[Alpha] Hey — this would be a good time to run the Dawn skill."
            branch = "nudge"
            chat = most_recent

            # Resurrect if cold
            if chat.state == ConversationState.COLD:
                mcp_servers = _create_mcp_servers(chat, app=app)
                await chat.resurrect(
                    system_prompt=app.state.system_prompt,
                    mcp_servers=mcp_servers,
                    disallowed_tools=DISALLOWED_INTERACTIVE,
                )

        span.set_attribute("dawn.branch", branch)
        span.set_attribute("dawn.chat_id", chat.id)

        # Send the prompt and collect the response
        from alpha_app.routes.enrobe import enrobe
        from alpha_app.routes.spans import format_input_messages, format_output_messages
        from alpha_app import AssistantEvent, ResultEvent, SystemEvent
        import json

        content = [{"type": "text", "text": prompt}]
        result = await enrobe(content, chat=chat, source="dawn")

        span.set_attribute("gen_ai.input.messages", json.dumps(
            format_input_messages(result.content)
        ))

        chat.begin_turn(content)
        await chat.send(result.content)

        # Collect response
        text_parts: list[str] = []
        output_blocks: list[dict] = []
        async for event in chat.events():
            if isinstance(event, SystemEvent) and event.subtype == "compact_boundary":
                chat._needs_orientation = True
                chat._injected_topics = set()
            elif isinstance(event, AssistantEvent):
                for block in event.content:
                    output_blocks.append(block)
                    if block.get("type") == "text" and block.get("text"):
                        text_parts.append(block["text"])
            elif isinstance(event, ResultEvent):
                if event.session_id:
                    chat.session_uuid = event.session_id
                span.set_attribute("gen_ai.usage.input_tokens", chat.total_input_tokens)
                span.set_attribute("gen_ai.usage.output_tokens", chat.output_tokens)
                span.set_attribute("gen_ai.response.model", chat.response_model or "")
                span.set_attribute("gen_ai.output.messages", json.dumps(
                    format_output_messages(output_blocks)
                ))
                break

        # Persist the chat
        from alpha_app.db import persist_chat
        await persist_chat(chat)

        response = "".join(text_parts).strip()
        logfire.info(
            "dawn: {branch} complete, {chars} chars",
            branch=branch,
            chars=len(response),
            chat_id=chat.id,
        )
        return branch


def add_cli_args(subparsers) -> None:
    """Register the dawn CLI subcommand."""
    sub = subparsers.add_parser("dawn", help="Run the morning bootstrap")
    sub.set_defaults(func=_cli_run)


async def _cli_run(args) -> None:
    """CLI entry point for `uv run job dawn`."""
    from alpha_app.db import init_pool, close_pool
    from alpha_app.memories import init_schema, close as close_cortex
    from alpha_app import assemble_system_prompt
    from alpha_app.topics import TopicRegistry
    from alpha_app.constants import JE_NE_SAIS_QUOI

    await init_pool()
    try:
        await init_schema()
    except Exception:
        pass

    topics_dir = JE_NE_SAIS_QUOI / "topics"
    topic_registry = TopicRegistry(topics_dir)
    topic_registry.scan()

    class FakeApp:
        class state:
            chats: dict = {}
            system_prompt: str = ""
            topic_registry = None

    app = FakeApp()
    app.state.system_prompt = await assemble_system_prompt()
    app.state.topic_registry = topic_registry

    result = await run(app, trigger="manual")
    print(f"Dawn result: {result}")

    await close_cortex()
    await close_pool()

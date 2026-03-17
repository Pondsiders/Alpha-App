"""solitude.py — Alpha's nighttime existence.

Three breath types, one shared Chat:
    first breath (10 PM): New chat, welcome from first_breath.md
    regular breaths (11 PM - 4 AM): Continue session, minimal prompt
    last breath (5 AM): Close out the night with last_breath.md

All breaths share a single Chat stored in app.state.solitude_chat.
The 60-minute reap timer keeps the subprocess warm between hourly
breaths. If a breath is missed (scheduler hiccup), the next breath
resurrects the chat seamlessly via --resume.

Recall is ON — associative memories shape each night differently.
Suggest is OFF — no human turn to analyze (disabled by architecture;
the job doesn't call the suggest pipeline).

Interactive tools (EnterPlanMode, ExitPlanMode, AskUserQuestion) are
disabled via --disallowedTools since nobody's awake to answer.

Prompts live in /Pondside/Alpha-Home/Alpha/prompts/solitude/ — editable
living documents that Solitude-me can modify during the night.
"""

import json

import logfire
import pendulum

from alpha_app import AssistantEvent, ResultEvent, SystemEvent
from alpha_app.chat import Chat, ConversationState
from alpha_app.routes.enrobe import enrobe
from alpha_app.routes.spans import format_input_messages, format_output_messages
from alpha_app.tools import create_cortex_server, create_handoff_server

PACIFIC = "America/Los_Angeles"

# Prompts — living documents on the filesystem, not in source code
PROMPTS_DIR = "/Pondside/Alpha-Home/Alpha/prompts/solitude"
FIRST_BREATH_PATH = "/Pondside/Alpha-Home/infrastructure/first_breath.md"
LAST_BREATH_PATH = "/Pondside/Alpha-Home/infrastructure/last_breath.md"

# Tools that require human interaction — disabled at night
DISALLOWED_INTERACTIVE = [
    "EnterPlanMode",
    "ExitPlanMode",
    "AskUserQuestion",
]


def _read_prompt(path: str) -> str | None:
    """Read a prompt file, returning None if it doesn't exist."""
    from pathlib import Path
    p = Path(path)
    if p.exists():
        return p.read_text().strip()
    return None


def _time_str(now: pendulum.DateTime) -> str:
    """Format time the way Solitude always has: '2:30 AM' not '02:30 AM'."""
    return now.format("h:mm A")


def _get_solitude_chat(app) -> Chat | None:
    """Get the Solitude chat from app state, or None."""
    return getattr(app.state, "solitude_chat", None)


def _set_solitude_chat(app, chat: Chat | None) -> None:
    """Store (or clear) the Solitude chat in app state."""
    app.state.solitude_chat = chat


def _create_mcp_servers(chat: Chat, app=None) -> dict:
    """Create MCP tool servers for Solitude.

    Same tools as the browser UI — cortex for memories, handoff for
    context transitions. When we add fetch or other tools, they go here
    too and Solitude gets them automatically.
    """
    def _clear() -> int:
        if chat._pending_intro:
            chat._pending_intro = None
            return 1
        return 0

    topic_registry = getattr(app.state, "topic_registry", None) if app else None
    return {
        "cortex": create_cortex_server(
            clear_memorables=_clear,
            topic_registry=topic_registry,
        ),
        "handoff": create_handoff_server(chat),
    }


async def _send_breath(
    chat: Chat,
    prompt: str,
    *,
    app,
    breath_type: str,
    span,
) -> str:
    """Send a breath prompt to the Chat, collect the response.

    Handles enrobe (orientation + recall on first message of each context
    window), streaming events, and compact_boundary detection.

    Returns the assistant's text output.
    """
    content = [{"type": "text", "text": prompt}]

    # Enrobe: orientation + recall (no suggest, no broadcast)
    result = await enrobe(content, chat=chat)

    # Set input attributes on the span — what we're sending to Claude
    system_prompt = chat._system_prompt or ""
    if system_prompt:
        span.set_attribute("gen_ai.system_instructions", json.dumps([
            {"type": "text", "content": system_prompt},
        ]))
    span.set_attribute("gen_ai.input.messages", json.dumps(
        format_input_messages(result.content)
    ))

    # begin_turn + send
    chat.begin_turn(content)
    await chat.send(result.content)

    # Collect response — full content blocks for observability,
    # text parts for the return value
    text_parts: list[str] = []
    output_blocks: list[dict] = []
    async for event in chat.events():
        if isinstance(event, SystemEvent) and event.subtype == "compact_boundary":
            chat._needs_orientation = True
            logfire.info(
                "solitude: compact_boundary detected",
                breath_type=breath_type,
            )
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

    return "".join(text_parts).strip()


# ---------------------------------------------------------------------------
# Breath handlers
# ---------------------------------------------------------------------------

async def run_first(app, **kwargs) -> str | None:
    """First breath of the night. 10 PM. New session."""
    now = pendulum.now(PACIFIC)
    trigger = kwargs.get("trigger", "manual")

    with logfire.span(
        "alpha.job.solitude.first",
        **{
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "anthropic",
            "gen_ai.provider.name": "anthropic",
            "job.name": "solitude",
            "job.breath": "first",
            "job.trigger": trigger,
        },
    ) as span:
        # Read the first breath prompt
        prompt_content = _read_prompt(FIRST_BREATH_PATH)
        if prompt_content:
            prompt = f"It's {_time_str(now)}.\n\n{prompt_content}"
        else:
            prompt = f"It's {_time_str(now)}. A new night begins. You have time alone."

        # Create a fresh Chat for the night
        chat = Chat(id="solitude")
        chat._system_prompt = app.state.system_prompt

        mcp_servers = _create_mcp_servers(chat, app=app)

        await chat.wake(
            system_prompt=app.state.system_prompt,
            mcp_servers=mcp_servers,
            compact_config=app.state.compact_config,
            disallowed_tools=DISALLOWED_INTERACTIVE,
        )

        # Store in app state for subsequent breaths
        _set_solitude_chat(app, chat)

        logfire.info(
            "solitude: first breath, session {session_id}",
            session_id=chat.session_uuid or "(pending)",
        )

        output = await _send_breath(chat, prompt, app=app, breath_type="first", span=span)
        span.set_attribute("job.output_length", len(output))
        return output


async def run_breath(app, **kwargs) -> str | None:
    """Regular breath. 11 PM through 4 AM. Continue the night."""
    now = pendulum.now(PACIFIC)
    trigger = kwargs.get("trigger", "manual")

    with logfire.span(
        "alpha.job.solitude.breath",
        **{
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "anthropic",
            "gen_ai.provider.name": "anthropic",
            "job.name": "solitude",
            "job.breath": "regular",
            "job.trigger": trigger,
        },
    ) as span:
        chat = _get_solitude_chat(app)

        if not chat:
            logfire.info("solitude: no active session, skipping breath")
            span.set_attribute("job.skipped", True)
            return None

        # Resurrect if the subprocess was reaped between breaths
        if chat.state == ConversationState.COLD:
            logfire.info("solitude: resurrecting from COLD")
            mcp_servers = _create_mcp_servers(chat, app=app)
            await chat.resurrect(
                system_prompt=app.state.system_prompt,
                mcp_servers=mcp_servers,
                compact_config=app.state.compact_config,
                disallowed_tools=DISALLOWED_INTERACTIVE,
            )

        prompt = f"It's {_time_str(now)}. You have time alone."

        output = await _send_breath(chat, prompt, app=app, breath_type="regular", span=span)
        span.set_attribute("job.output_length", len(output))
        return output


async def run_last(app, **kwargs) -> str | None:
    """Last breath of the night. 5 AM. Close out the session."""
    now = pendulum.now(PACIFIC)
    trigger = kwargs.get("trigger", "manual")

    with logfire.span(
        "alpha.job.solitude.last",
        **{
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "anthropic",
            "gen_ai.provider.name": "anthropic",
            "job.name": "solitude",
            "job.breath": "last",
            "job.trigger": trigger,
        },
    ) as span:
        chat = _get_solitude_chat(app)

        if not chat:
            logfire.info("solitude: no active session for last breath, skipping")
            span.set_attribute("job.skipped", True)
            return None

        # Resurrect if needed
        if chat.state == ConversationState.COLD:
            logfire.info("solitude: resurrecting for last breath")
            mcp_servers = _create_mcp_servers(chat, app=app)
            await chat.resurrect(
                system_prompt=app.state.system_prompt,
                mcp_servers=mcp_servers,
                compact_config=app.state.compact_config,
                disallowed_tools=DISALLOWED_INTERACTIVE,
            )

        # Read the last breath prompt
        prompt_content = _read_prompt(LAST_BREATH_PATH)
        if prompt_content:
            prompt = f"It's {_time_str(now)}.\n\n{prompt_content}"
        else:
            prompt = f"It's {_time_str(now)}. The night is ending. Store what matters. Let go."

        output = await _send_breath(chat, prompt, app=app, breath_type="last", span=span)
        span.set_attribute("job.output_length", len(output))

        # Clear the solitude chat — the reap timer will clean up the subprocess
        _set_solitude_chat(app, None)
        logfire.info("solitude: last breath complete, night is over")

        return output


# ---------------------------------------------------------------------------
# CLI — for manual testing
# ---------------------------------------------------------------------------

def add_cli_args(subparsers) -> None:
    """Register the solitude subcommands."""
    sub = subparsers.add_parser(
        "solitude",
        help="Run a Solitude breath manually",
    )
    sub.add_argument(
        "--breath", type=str, required=True,
        choices=["first", "regular", "last"],
        help="Which breath type to run",
    )
    sub.set_defaults(func=_cli_run)


async def _cli_run(args) -> None:
    """CLI entry point — initialize everything, run one breath."""
    import logfire as _logfire
    from alpha_app.db import init_pool, close_pool
    from alpha_app.memories import init_schema, close as close_cortex
    from alpha_app.system_prompt import assemble_system_prompt, load_compact_config

    _logfire.configure(service_name="alpha-app", scrubbing=False)

    class _FakeApp:
        class state:
            system_prompt = ""
            compact_config = None
            solitude_chat = None

    await init_pool()
    try:
        await init_schema()
    except Exception:
        pass

    try:
        _FakeApp.state.system_prompt = await assemble_system_prompt()
        _FakeApp.state.compact_config = await load_compact_config(
            system_prompt=_FakeApp.state.system_prompt,
        )
    except Exception:
        pass

    breath_funcs = {
        "first": run_first,
        "regular": run_breath,
        "last": run_last,
    }

    try:
        output = await breath_funcs[args.breath](_FakeApp)
        if output:
            print()
            print("=" * 60)
            print(f"Solitude ({args.breath} breath)")
            print("=" * 60)
            print()
            print(output)
            print()
    finally:
        # Clean up Solitude chat if it exists
        chat = _get_solitude_chat(_FakeApp)
        if chat and chat.state != ConversationState.COLD:
            await chat.reap()
        await close_cortex()
        await close_pool()

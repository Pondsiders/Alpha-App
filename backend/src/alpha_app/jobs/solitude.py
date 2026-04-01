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
from alpha_app.chat import Chat, ConversationState, find_circadian_chat, generate_chat_id
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
        prompt_file: foo.md       -> prompts: ["foo.md"]
        prompts: [foo.md, bar.md] -> prompts: ["foo.md", "bar.md"]
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

    with logfire.span("alpha.job.solitude.breath", **{
        "job.name": "solitude",
        "job.breath_index": entry_index,
        "job.prompts": entry.prompts,
        "job.trigger": kwargs.get("trigger", "scheduled"),
    }) as span:

        # -- Do the breath FIRST (work before schedule) --
        # Find the circadian day's chat (6 AM to 6 AM, not midnight to midnight)
        chat = find_circadian_chat(getattr(app.state, "chats", {}))
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

        # -- Work succeeded — now schedule what's next --
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


    # _find_todays_chat removed — replaced by find_circadian_chat() in chat.py


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
    """Wait for Claude to finish, set observability attributes.

    Works regardless of whether a WebSocket listener is attached —
    wait_until_ready() uses the Claude-level _ready Event, not the
    event stream or conversation state.
    """
    await chat._claude.wait_until_ready()

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

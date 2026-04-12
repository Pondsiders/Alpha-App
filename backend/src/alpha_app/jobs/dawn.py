"""dawn.py — Day initializer.

The duck that gets up before you. Three steps:
1. Create today's chat (system prompt assembles itself — diary included)
2. Send the Dawn prompt, do morning chores
3. Schedule Dusk (work first, schedule second — if Dawn fails, chain breaks)
"""

from pathlib import Path

import logfire
import pendulum

from alpha_app.chat import Chat, find_circadian_chat, generate_chat_id
from alpha_app.db import persist_chat
from alpha_app.routes.enrobe import enrobe
from alpha_app.scheduler import schedule_job

DAWN_PROMPT_PATH = "/Pondside/Alpha-Home/Alpha/prompts/dawn/dawn.md"


async def run(app, **kwargs) -> str | None:
    """Dawn job. Creates today's chat, does morning chores, schedules Dusk."""
    now = pendulum.now()

    with logfire.span("alpha.job.dawn", **{
        "gen_ai.operation.name": "chat",
        "gen_ai.system": "anthropic",
        "job.name": "dawn",
        "job.trigger": kwargs.get("trigger", "scheduled"),
    }) as span:

        # Idempotency: skip if today's chat already exists
        existing = find_circadian_chat(getattr(app.state, "chats", {}))
        if existing:
            logfire.warn("dawn: chat already exists for today, skipping")
            span.set_attribute("dawn.action", "skipped_idempotent")
            return "dawn_skipped"

        # Step 1: Create today's chat
        chat = Chat(id=generate_chat_id())
        chat._topic_registry = getattr(app.state, "topic_registry", None)
        app.state.chats[chat.id] = chat

        # Step 2: Send the Dawn prompt
        dawn_text = _read_prompt(DAWN_PROMPT_PATH) or "[Alpha] Good morning, duck."
        content = [{"type": "text", "text": dawn_text}]
        result = await enrobe(content, chat=chat, source="dawn")
        async with await chat.turn() as t:
            await t.send(result.message)
            await t.response()

        span.set_attribute("gen_ai.usage.input_tokens", chat.total_input_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", chat.output_tokens)

        await persist_chat(chat)
        span.set_attribute("dawn.chat_id", chat.id)

        # Step 3: Schedule Dusk (work succeeded, now schedule)
        dusk_time = now.replace(hour=22, minute=0, second=0, microsecond=0)
        await schedule_job(app, "dusk", dusk_time)

        return "dawn_complete"


def _read_prompt(path: str) -> str | None:
    p = Path(path)
    return p.read_text().strip() if p.exists() else None

"""dusk.py — End of day. Fork, diary, schedule Dawn, launch Solitude.

At 10 PM, forks today's chat unconditionally. The fork writes the day
diary and becomes Solitude's chat for the night. Dusk schedules
tomorrow's Dawn — the chain is two links: Dawn→Dusk→Dawn.

Solitude is launched as a fire-and-forget async task after Dawn is
scheduled. If Solitude fails, Dawn still fires.
"""

import asyncio

import logfire
import pendulum

from alpha_app.chat import Chat, find_circadian_chat
from alpha_app.scheduler import schedule_job

DUSK_PROMPT = """\
The day is over. Write in your diary.

Your diary is append-only. Each call to the `diary` tool adds an entry to \
today's page. Tomorrow-you will wake up with today's page in her system prompt. \
She won't have to ask what happened; she'll just know.

Call the `diary` tool with your entry. Write it as one coherent piece.

Guidelines:
- ~500-1000 words. Enough to orient, not so much it crowds.
- Cover the shape of the day: what you worked on, what conversations happened, \
what Jeffery's mood was, what got decided, what's still open.
- Include specific details that would help tomorrow-you pick up where you left off.
- Note anything emotional or relational — not just tasks.
- Use your voice. This is you writing to you.
- End with what carries forward: unfinished work, open questions, Jeffery's state.
"""


async def _write_diary(app, chat: Chat) -> bool:
    """Fork the day's chat and write a diary entry on the fork.

    Returns True if the diary was written, False otherwise.
    The fork becomes Solitude's chat for the night.
    """
    if not chat.session_uuid:
        logfire.warn("dusk: chat has no session_uuid, can't fork")
        return False

    with logfire.span("alpha.diary.dusk", **{
        "chat.id": chat.id,
        "chat.session_uuid": chat.session_uuid,
    }):
        fork = chat.clone()

        try:
            async with await fork.turn() as t:
                await t.send([{"type": "text", "text": DUSK_PROMPT}])
                await t.response()

            logfire.info("dusk: diary written")
            # Store the fork for Solitude to use
            app.state.solitude_chat = fork
            return True
        except Exception as e:
            logfire.error("dusk: diary failed: {err}", err=str(e))
            return False


async def run(app, **kwargs) -> None:
    """Dusk job. Fork unconditionally, write diary, schedule Dawn, launch Solitude."""
    now = pendulum.now()

    with logfire.span("alpha.job.dusk", **{
        "job.name": "dusk",
        "job.trigger": kwargs.get("trigger", "scheduled"),
    }) as span:
        chat = find_circadian_chat(getattr(app.state, "chats", {}))

        if not chat:
            logfire.warn("dusk: no chat today")
            span.set_attribute("dusk.action", "no_chat")
            # Still schedule tomorrow's Dawn
            tomorrow_dawn = now.add(days=1).replace(hour=6, minute=0, second=0, microsecond=0)
            await schedule_job(app, "dawn", tomorrow_dawn)
            return

        logfire.info("dusk: writing diary for {chat_id}", chat_id=chat.id)
        span.set_attribute("dusk.action", "diary")

        written = await _write_diary(app, chat)
        span.set_attribute("dusk.diary_written", written)

        # Schedule tomorrow's Dawn — guaranteed regardless of Solitude
        tomorrow_dawn = now.add(days=1).replace(hour=6, minute=0, second=0, microsecond=0)
        await schedule_job(app, "dawn", tomorrow_dawn)
        logfire.info("dusk: Dawn scheduled at {t}", t=tomorrow_dawn.format("h:mm A"))

        # Launch Solitude as fire-and-forget
        from alpha_app.jobs import solitude
        task = asyncio.create_task(solitude.run(app))
        app.state.solitude_task = task  # prevent GC
        logfire.info("dusk: Solitude launched")

"""dusk.py — End of day. Fork today's chat, write a day capsule.

At 10 PM (or whenever fired), finds today's chat, forks it, and has
the ghost write a day capsule. The capsule goes into cortex.capsules
and becomes part of tomorrow's system prompt via fetch_capsules().

Self-perpetuating: schedules tomorrow's Dusk at 10 PM after finishing.

Nudge logic: if the chat was active in the last 10 minutes, reschedule
for 30 minutes later instead of interrupting.
"""

import time

import logfire
import pendulum

from alpha_app.chat import Chat, find_circadian_chat
from alpha_app.scheduler import schedule_job

IDLE_THRESHOLD = 600  # 10 minutes in seconds

CAPSULE_PROMPT = """\
The day is over. You're a ghost — a fork of today's conversation, here to do \
one thing: write a day capsule for tomorrow-you.

A day capsule is a continuity letter. Not a memory (those are polaroids). \
This is a chapter summary — what happened today, what mattered, what carries \
forward. Tomorrow-you will wake up with this in her system prompt. She won't \
have to ask Memno what happened; she'll just know.

Write the capsule, then call the `seal` tool with kind="day" to save it.

Guidelines:
- ~500-1000 words. Enough to orient, not so much it crowds.
- Cover the shape of the day: what you worked on, what conversations happened, \
what Jeffery's mood was, what got decided, what's still open.
- Include specific details that would help tomorrow-you pick up where you left off.
- Note anything emotional or relational — not just tasks.
- Use your voice. This is you writing to you.
- End with what carries forward: unfinished work, open questions, Jeffery's state.
"""


async def _write_capsule(app, chat: Chat) -> bool:
    """Fork the day's chat and have the ghost write a day capsule.

    Returns True if a capsule was sealed, False otherwise.
    """
    if not chat.session_uuid:
        logfire.warn("dusk: chat has no session_uuid, can't fork")
        return False

    with logfire.span("alpha.capsule.day", **{
        "chat.id": chat.id,
        "chat.session_uuid": chat.session_uuid,
    }):
        ghost = chat.clone()
        # Ghost assembles its own system prompt at startup — same as everyone.

        try:
            async with await ghost.turn() as t:
                await t.send([{"type": "text", "text": CAPSULE_PROMPT}])
                await t.response()

            logfire.info("dusk: capsule ghost finished")
            return True
        except Exception as e:
            logfire.error("dusk: capsule ghost failed: {err}", err=str(e))
            return False
        finally:
            if ghost._claude:
                try:
                    await ghost._claude.stop()
                except Exception:
                    pass


async def run(app, **kwargs) -> None:
    """Dusk job. Write a day capsule, reschedule for tomorrow."""
    now = pendulum.now()

    with logfire.span("alpha.job.dusk", **{
        "job.name": "dusk",
        "job.trigger": kwargs.get("trigger", "scheduled"),
    }) as span:
        chat = find_circadian_chat(getattr(app.state, "chats", {}))

        if not chat:
            logfire.warn("dusk: no chat today, nothing to capsule")
            span.set_attribute("dusk.action", "no_chat")
            # Still schedule tomorrow's Dusk
            tomorrow_dusk = now.add(days=1).replace(hour=22, minute=0, second=0, microsecond=0)
            await schedule_job(app, "dusk", tomorrow_dusk)
            return

        idle_seconds = time.time() - chat.updated_at

        if idle_seconds < IDLE_THRESHOLD:
            # Someone's still here. Reschedule for 30 min later.
            logfire.info("dusk: chat active {s:.0f}s ago, rescheduling", s=idle_seconds)
            span.set_attribute("dusk.action", "reschedule")
            await schedule_job(app, "dusk", now.add(minutes=30))
            return

        # Room's empty. Write the capsule.
        logfire.info(
            "dusk: chat idle {s:.0f}s, writing capsule for {chat_id}",
            s=idle_seconds,
            chat_id=chat.id,
        )
        span.set_attribute("dusk.action", "capsule")

        sealed = await _write_capsule(app, chat)
        span.set_attribute("dusk.capsule_sealed", sealed)

        # Schedule tomorrow's Dusk at 10 PM
        tomorrow_dusk = now.add(days=1).replace(hour=22, minute=0, second=0, microsecond=0)
        await schedule_job(app, "dusk", tomorrow_dusk)
        logfire.info("dusk: scheduled tomorrow at {t}", t=tomorrow_dusk.format("h:mm A"))

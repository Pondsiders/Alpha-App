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
                "Solitude's waiting whenever you're ready. No rush. \U0001f986"
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

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
    message = kwargs.get("message", "\u23f0")

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

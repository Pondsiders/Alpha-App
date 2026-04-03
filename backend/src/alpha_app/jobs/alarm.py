"""alarm.py — Custom one-shot alarm.

Drops a message into today's most recent chat via interject().
Fire and forget — bypasses the turn lock because alarms are
time-sensitive and can't wait for a 15-minute turn to finish.
Auto-starts Claude if cold (via interject → _ensure_claude).
"""

import logfire

from alpha_app.chat import find_circadian_chat


async def run(app, **kwargs) -> None:
    """Alarm handler. Interject the message into today's active chat."""
    message = kwargs.get("message", "\u23f0")

    with logfire.span("alpha.job.alarm", **{
        "job.name": "alarm",
        "job.message": message,
    }):
        chat = find_circadian_chat(getattr(app.state, "chats", {}))
        if not chat:
            logfire.warn("alarm: no chat today, message lost: {msg}", msg=message)
            return

        # Store system prompt so _ensure_claude can use it
        chat._system_prompt = await app.state.get_system_prompt()

        content = [{"type": "text", "text": f"[Alpha] {message}"}]
        await chat.interject(content)  # auto-starts Claude if cold
        logfire.info("alarm: interjected to chat {chat_id}", chat_id=chat.id)

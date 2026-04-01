"""alarm.py — Custom one-shot alarm.

Drops a message into today's most recent chat via interject().
Fire and forget — bypasses the turn lock because alarms are
time-sensitive and can't wait for a 15-minute turn to finish.
"""

import logfire

from alpha_app.chat import Chat, ConversationState, find_circadian_chat


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

        if chat.state == ConversationState.COLD:
            from alpha_app.tools import create_alpha_server
            topic_registry = getattr(app.state, "topic_registry", None)
            mcp_servers = {"alpha": create_alpha_server(chat=chat, topic_registry=topic_registry)}
            await chat.resurrect(
                system_prompt=app.state.system_prompt,
                mcp_servers=mcp_servers,
            )

        content = [{"type": "text", "text": f"[Alpha] {message}"}]
        await chat.interject(content)
        logfire.info("alarm: interjected to chat {chat_id}", chat_id=chat.id)

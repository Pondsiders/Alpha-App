"""alarm.py — Custom one-shot alarm.

Drops a message into today's most recent chat. Fire and forget.
"""

import time

import logfire
import pendulum

from alpha_app.chat import Chat, ConversationState
from alpha_app import ResultEvent


async def run(app, **kwargs) -> None:
    """Alarm handler. Deliver the message to today's active chat.

    If the chat has an on_event callback (live WebSocket session), we
    just send and walk away — the callback handles the response and
    broadcasts it to the frontend. We don't drain events ourselves.

    If the chat has NO callback (headless, e.g. no browser open), we
    drain events until ResultEvent for observability.
    """
    message = kwargs.get("message", "\u23f0")

    with logfire.span("alpha.job.alarm", **{
        "job.name": "alarm",
        "job.message": message,
    }) as span:
        chat = _find_todays_most_recent_chat(app)
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
        chat.begin_turn(content)
        await chat.send(content)

        if chat.on_broadcast:
            # Live session — the callback handles everything.
            # Just log that we sent it and walk away.
            logfire.info("alarm: sent to live chat {chat_id}, callback handles response",
                        chat_id=chat.id)
        else:
            # Headless — drain for observability
            async for event in chat.events():
                if isinstance(event, ResultEvent):
                    break
            logfire.info("alarm: sent to headless chat {chat_id}", chat_id=chat.id)


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

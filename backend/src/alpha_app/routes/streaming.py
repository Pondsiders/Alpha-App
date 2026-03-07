"""streaming.py — Stream events from a Chat to all connected clients.

Handles the inner event loop: reads from chat.events(), broadcasts
text deltas, thinking deltas, tool calls, context updates, and the
final result to every connected WebSocket.
"""

import json

from alpha_sdk import AssistantEvent, ErrorEvent, ResultEvent, StreamEvent

from alpha_app.chat import Chat, ChatState
from alpha_app.db import persist_chat
from alpha_app.routes.broadcast import broadcast
from alpha_app.routes.spans import set_turn_span_response


async def stream_chat_events(connections: set, chat: Chat, span=None) -> None:
    """Stream events from a Chat to ALL connected clients.

    All emitted events carry the chatId and broadcast to every connection.
    On turn completion, emits chat-state with the updated state, then done.
    """
    chat_id = chat.id
    turn_completed = False
    last_token_count = chat.token_count
    output_parts: list[dict] = []

    try:
        async for event in chat.events():
            # Real-time context updates
            current_tokens = chat.token_count
            if current_tokens != last_token_count:
                last_token_count = current_tokens
                try:
                    await broadcast(connections, {
                        "type": "context-update",
                        "chatId": chat_id,
                        "data": {
                            "tokenCount": current_tokens,
                            "tokenLimit": chat.context_window,
                        },
                    })
                except Exception:
                    pass

            if isinstance(event, StreamEvent):
                if event.delta_type == "text_delta":
                    text = event.delta_text
                    if text:
                        await broadcast(connections, {"type": "text-delta", "chatId": chat_id, "data": text})
                elif event.delta_type == "thinking_delta":
                    text = event.delta_text
                    if text:
                        await broadcast(connections, {"type": "thinking-delta", "chatId": chat_id, "data": text})

            elif isinstance(event, AssistantEvent):
                output_parts.extend(event.content)
                for block in event.content:
                    if block.get("type") == "tool_use":
                        await broadcast(connections, {
                            "type": "tool-call",
                            "chatId": chat_id,
                            "data": {
                                "toolCallId": block.get("id", ""),
                                "toolName": block.get("name", ""),
                                "args": block.get("input", {}),
                                "argsText": json.dumps(block.get("input", {})),
                            },
                        })

            elif isinstance(event, ResultEvent):
                await persist_chat(chat)

                if span:
                    set_turn_span_response(span, chat, event, output_parts)

                await broadcast(connections, {
                    "type": "chat-state",
                    "chatId": chat_id,
                    "data": {
                        "state": chat.state.value,
                        "title": chat.title,
                        "updatedAt": chat.updated_at,
                        "sessionUuid": chat.session_uuid or "",
                        "tokenCount": chat.token_count,
                        "contextWindow": chat.context_window,
                    },
                })

                turn_completed = True
                break

            elif isinstance(event, ErrorEvent):
                await broadcast(connections, {"type": "error", "chatId": chat_id, "data": event.message})

    except Exception as e:
        if span:
            span.set_attribute("error.type", type(e).__name__)
        try:
            await broadcast(connections, {"type": "error", "chatId": chat_id, "data": str(e)})
        except Exception:
            pass

    finally:
        if not turn_completed and chat.state == ChatState.BUSY:
            await chat.reap()

    try:
        await broadcast(connections, {"type": "done", "chatId": chat_id})
    except Exception:
        pass

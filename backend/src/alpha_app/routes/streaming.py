"""streaming.py — Stream events from a Chat to all connected clients.

Handles the inner event loop: reads from chat.events(), broadcasts
text deltas, thinking deltas, tool calls, context updates, approach
light interjections, and the final result to every connected WebSocket.
"""

import json

import logfire
from alpha_sdk import AssistantEvent, ErrorEvent, ResultEvent, StreamEvent

from alpha_app.chat import Chat, ConversationState
from alpha_app.db import persist_chat
from alpha_app.routes.broadcast import broadcast
from alpha_app.routes.spans import set_turn_span_response

# Approach light warning messages — injected as interjections mid-turn.
_APPROACH_WARNINGS = {
    "yellow": (
        "[Context: yellow] Context window is 65% full. "
        "Start wrapping up — store important memories while there's room."
    ),
    "red": (
        "[Context: red] Context window is 75% full. Compaction is imminent. "
        "Store critical memories NOW and prepare for handoff."
    ),
}


async def stream_chat_events(connections: set, chat: Chat, span=None) -> None:
    """Stream events from a Chat to ALL connected clients.

    All emitted events carry the chatId and broadcast to every connection.
    On turn completion, emits chat-state with the updated state, then done.
    """
    chat_id = chat.id
    turn_completed = False
    last_token_count = chat.token_count
    output_parts: list[dict] = []
    interjection_count = 0

    try:
        async for event in chat.events():
            # Real-time context updates + async approach lights
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

                # Approach light: check if a threshold was just crossed.
                # Each fires exactly once — yellow at 65%, red at 75%.
                # Drops an interjection into the running conversation so
                # Alpha gets the warning mid-turn, between tool calls.
                threshold = chat.check_approach_threshold()
                if threshold is not None:
                    warning = _APPROACH_WARNINGS[threshold]
                    logfire.info(
                        "approach light: {threshold}",
                        threshold=threshold,
                        chat_id=chat_id,
                        token_count=current_tokens,
                        context_window=chat.context_window,
                    )
                    try:
                        await chat.send([{"type": "text", "text": warning}])
                        interjection_count += 1
                    except Exception:
                        pass  # Don't crash streaming if interjection fails
                    try:
                        await broadcast(connections, {
                            "type": "approach-light",
                            "chatId": chat_id,
                            "data": {"level": threshold, "text": warning},
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
                        "state": chat.state.wire_value,
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

        # Drain interjection responses. Each approach light interjection
        # triggers a new subprocess turn; consume those events silently
        # so they don't contaminate the next user message's event stream.
        for _ in range(interjection_count):
            async for drain_event in chat.events():
                if isinstance(drain_event, ResultEvent):
                    break

    except Exception as e:
        if span:
            span.set_attribute("error.type", type(e).__name__)
        try:
            await broadcast(connections, {"type": "error", "chatId": chat_id, "data": str(e)})
        except Exception:
            pass

    finally:
        if not turn_completed and chat.state in (ConversationState.ENRICHING, ConversationState.RESPONDING):
            await chat.reap()

    try:
        await broadcast(connections, {"type": "done", "chatId": chat_id})
    except Exception:
        pass

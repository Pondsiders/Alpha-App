"""streaming.py — Stream events from a Chat to all connected clients.

Handles the inner event loop: reads from chat.events(), broadcasts
text deltas, thinking deltas, tool calls, context updates, approach
light interjections, and the final result to every connected WebSocket.
"""

import json

import logfire
from alpha_app import AssistantEvent, ErrorEvent, ResultEvent, StreamEvent, SystemEvent, UserEvent

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


async def _check_approach_light(
    chat: Chat,
    connections: set,
    chat_id: str,
    last_token_count: int,
) -> tuple[int, int]:
    """Check for threshold crossing, fire interjection + broadcast if needed.

    Monitors chat.token_count for changes. When a new approach light
    threshold is crossed, sends the warning as an interjection to claude
    and broadcasts the approach-light event to all connected clients.

    Returns (updated_last_token_count, interjections_sent).
    """
    current_tokens = chat.token_count
    interjections_sent = 0

    if current_tokens != last_token_count:
        last_token_count = current_tokens

        # Context meter updates ride on the coalesced assistant-message event.
        # No separate context-update broadcast needed.

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
                interjections_sent = 1
            except Exception:
                pass
            try:
                await broadcast(connections, {
                    "type": "approach-light",
                    "chatId": chat_id,
                    "data": {"level": threshold, "text": warning},
                })
            except Exception:
                pass

    return last_token_count, interjections_sent


async def stream_chat_events(connections: set, chat: Chat, span=None) -> str:
    """Stream events from a Chat to ALL connected clients.

    All emitted events carry the chatId and broadcast to every connection.
    On turn completion, emits chat-state with the updated state, then done.

    Returns:
        The assistant's text output from this turn (for suggest pipeline).
    """
    chat_id = chat.id
    turn_completed = False
    last_token_count = chat.token_count
    output_parts: list[dict] = []
    interjection_count = 0

    # Accumulate assistant message parts for the coalesced event.
    # Each part is {"type": "thinking"|"text"|"tool-call", ...}.
    # Built from stream deltas and assistant events as they arrive.
    wire_parts: list[dict] = []

    try:
        async for event in chat.events():
            # Real-time context updates + async approach lights
            last_token_count, new_interjections = await _check_approach_light(
                chat, connections, chat_id, last_token_count,
            )
            interjection_count += new_interjections

            if isinstance(event, SystemEvent) and event.subtype == "compact_boundary":
                chat._needs_orientation = True
                logfire.info(
                    "compact_boundary detected",
                    chat_id=chat_id,
                    subtype=event.subtype,
                )

            elif isinstance(event, UserEvent):
                # Broadcast user echoes from --replay-user-messages.
                # Ephemeral — not stored in Postgres (the enriched version
                # from enrobe is already stored).
                await broadcast(connections, {
                    "type": "user-message",
                    "chatId": chat_id,
                    "data": {"content": event.content},
                }, persist=False)

            elif isinstance(event, StreamEvent):
                if event.delta_type == "text_delta":
                    text = event.delta_text
                    if text:
                        # Broadcast live delta (ephemeral — not stored)
                        await broadcast(connections, {
                            "type": "text-delta", "chatId": chat_id, "data": text,
                        }, persist=False)
                        # Accumulate into wire_parts
                        if wire_parts and wire_parts[-1]["type"] == "text":
                            wire_parts[-1]["text"] += text
                        else:
                            wire_parts.append({"type": "text", "text": text})
                elif event.delta_type == "thinking_delta":
                    text = event.delta_text
                    if text:
                        await broadcast(connections, {
                            "type": "thinking-delta", "chatId": chat_id, "data": text,
                        }, persist=False)
                        if wire_parts and wire_parts[-1]["type"] == "thinking":
                            wire_parts[-1]["thinking"] += text
                        else:
                            wire_parts.append({"type": "thinking", "thinking": text})

            elif isinstance(event, AssistantEvent):
                output_parts.extend(event.content)
                for block in event.content:
                    if block.get("type") == "tool_use":
                        tool_data = {
                            "toolCallId": block.get("id", ""),
                            "toolName": block.get("name", ""),
                            "args": block.get("input", {}),
                            "argsText": json.dumps(block.get("input", {})),
                        }
                        # Broadcast live tool call (ephemeral)
                        await broadcast(connections, {
                            "type": "tool-call",
                            "chatId": chat_id,
                            "data": tool_data,
                        }, persist=False)
                        # Accumulate into wire_parts
                        wire_parts.append({"type": "tool-call", **tool_data})

            elif isinstance(event, ResultEvent):
                await persist_chat(chat)

                if span:
                    set_turn_span_response(span, chat, event, output_parts)

                # Emit the coalesced assistant-message (persisted for replay).
                # Includes context window info so the frontend can sync the meter.
                if wire_parts:
                    await broadcast(connections, {
                        "type": "assistant-message",
                        "chatId": chat_id,
                        "data": {
                            "parts": wire_parts,
                            "tokenCount": chat.token_count,
                            "contextWindow": chat.context_window,
                        },
                    })

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
                }, persist=False)

                turn_completed = True
                break

            elif isinstance(event, ErrorEvent):
                await broadcast(connections, {"type": "error", "chatId": chat_id, "data": event.message})

        # Drain interjection responses, allowing cascading thresholds.
        # Each interjection triggers a subprocess turn whose response may
        # push past the next threshold. Yellow's response can trigger red.
        # With two thresholds the cascade is bounded to at most 2 levels.
        pending_drains = interjection_count
        while pending_drains > 0:
            pending_drains -= 1
            async for drain_event in chat.events():
                last_token_count, new_interjections = await _check_approach_light(
                    chat, connections, chat_id, last_token_count,
                )
                pending_drains += new_interjections
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

    # Return assistant text for suggest pipeline
    return " ".join(
        block.get("text", "")
        for block in output_parts
        if block.get("type") == "text"
    )

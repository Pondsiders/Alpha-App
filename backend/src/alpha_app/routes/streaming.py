"""streaming.py — Stream events from a Chat to all connected clients.

Handles the inner event loop: reads from chat.events(), broadcasts
text deltas, thinking deltas, tool calls, context updates, approach
light interjections, and the final result to every connected WebSocket.

Assembles an AssistantMessage progressively as events arrive — the same
pattern as enrobe.py building a UserMessage from parts.
"""

import json
import uuid

import logfire
from alpha_app import AssistantEvent, ErrorEvent, ResultEvent, StreamEvent, SystemEvent, UserEvent

from alpha_app.chat import Chat, ConversationState
from alpha_app.db import persist_chat
from alpha_app.models import AssistantMessage
from alpha_app.routes.broadcast import broadcast

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


async def stream_chat_events(connections: set, chat: Chat, span=None) -> AssistantMessage:
    """Stream events from a Chat to ALL connected clients.

    Assembles an AssistantMessage progressively as events arrive.
    All emitted events carry the chatId and broadcast to every connection.
    On turn completion, emits chat-state with the updated state, then done.

    Returns:
        The completed AssistantMessage with parts, token counts, and metadata.
    """
    chat_id = chat.id
    turn_completed = False
    last_token_count = chat.token_count
    interjection_count = 0

    # Reset output token accumulator for this turn. The proxy accumulates
    # with += across all API calls; only we know when a new turn begins.
    chat.reset_output_tokens()

    # The message being assembled — the lazy susan
    msg = AssistantMessage(id=f"msg-{uuid.uuid4().hex[:12]}")

    # Raw output parts for Logfire gen_ai.output.messages (Messages API format).
    # Separate from msg.parts which uses wire format.
    output_parts: list[dict] = []

    try:
        async for event in chat.events():
            # Real-time context updates + async approach lights
            last_token_count, new_interjections = await _check_approach_light(
                chat, connections, chat_id, last_token_count,
            )
            interjection_count += new_interjections

            if isinstance(event, SystemEvent) and event.subtype == "compact_boundary":
                chat._needs_orientation = True
                chat._injected_topics = set()
                # Clear the recall seen-cache — post-compact, old memories
                # may be relevant again in the new context window.
                from alpha_app.memories.recall import clear_seen
                clear_seen(chat_id)
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
                        # Accumulate into AssistantMessage parts
                        if msg.parts and msg.parts[-1]["type"] == "text":
                            msg.parts[-1]["text"] += text
                        else:
                            msg.parts.append({"type": "text", "text": text})
                elif event.delta_type == "thinking_delta":
                    text = event.delta_text
                    if text:
                        await broadcast(connections, {
                            "type": "thinking-delta", "chatId": chat_id, "data": text,
                        }, persist=False)
                        if msg.parts and msg.parts[-1]["type"] == "thinking":
                            msg.parts[-1]["thinking"] += text
                        else:
                            msg.parts.append({"type": "thinking", "thinking": text})

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
                        # Accumulate into AssistantMessage parts
                        msg.parts.append({"type": "tool-call", **tool_data})

            elif isinstance(event, ResultEvent):
                await persist_chat(chat)

                # Snapshot token counts and metadata onto the message
                msg.input_tokens = chat.total_input_tokens
                msg.output_tokens = chat.output_tokens
                msg.cache_creation_tokens = chat.cache_creation_tokens
                msg.cache_read_tokens = chat.cache_read_tokens
                msg.context_window = chat.context_window
                msg.model = chat.response_model
                msg.stop_reason = chat.stop_reason
                msg.cost_usd = event.cost_usd
                msg.duration_ms = event.duration_ms
                msg.inference_count = event.num_turns

                if span:
                    _set_turn_span_response(span, msg, chat, output_parts)

                # Emit the coalesced assistant-message (persisted for replay).
                if msg.parts:
                    await broadcast(connections, {
                        "type": "assistant-message",
                        "chatId": chat_id,
                        "data": msg.to_wire(),
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

    return msg


def _set_turn_span_response(span, msg: AssistantMessage, chat: Chat, output_parts: list) -> None:
    """Set gen_ai response attributes on the turn span.

    Reads from the AssistantMessage for token counts and metadata,
    from the Chat for quota usage, and from output_parts for the
    Logfire gen_ai.output.messages format.
    """
    from alpha_app.routes.spans import format_output_messages

    span.set_attribute("gen_ai.response.model", msg.model or "")
    span.set_attribute("gen_ai.usage.input_tokens", msg.input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", msg.output_tokens)
    span.set_attribute("gen_ai.usage.cache_creation.input_tokens", msg.cache_creation_tokens)
    span.set_attribute("gen_ai.usage.cache_read.input_tokens", msg.cache_read_tokens)

    output_messages = format_output_messages(output_parts)
    span.set_attribute("gen_ai.output.messages", output_messages)

    span.set_attribute("gen_ai.response.id", chat.response_id or "")
    span.set_attribute("gen_ai.response.finish_reasons", [msg.stop_reason or "unknown"])
    span.set_attribute("gen_ai.token_count", msg.input_tokens)
    span.set_attribute("cost_usd", msg.cost_usd)
    span.set_attribute("duration_ms", msg.duration_ms)
    span.set_attribute("inference_count", msg.inference_count)
    span.set_attribute("response_length", sum(
        len(p.get("content", ""))
        for m in output_messages
        for p in m.get("parts", [])
    ))

    if chat.usage_5h is not None:
        span.set_attribute("anthropic.quota.usage_5h", chat.usage_5h)
    if chat.usage_7d is not None:
        span.set_attribute("anthropic.quota.usage_7d", chat.usage_7d)

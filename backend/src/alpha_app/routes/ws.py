"""WebSocket route — bidirectional multi-chat pipe.

Phase 2: All messages carry chatId. Multiple chats multiplexed over one connection.

Client -> Server messages:
  { "type": "create-chat" }
  { "type": "list-chats" }
  { "type": "send", "chatId": "...", "content": "Hello" }
  { "type": "send", "chatId": "...", "content": [{ "type": "text", "text": "Hello" }, ...] }
  { "type": "interrupt", "chatId": "..." }

Server -> Client messages:
  { "type": "chat-created", "chatId": "...", "data": { "state": "idle" } }
  { "type": "chat-list", "data": [...] }
  { "type": "chat-state", "chatId": "...", "data": { "state": "busy", "title": "...", "updatedAt": ..., "sessionUuid": "...", "tokenCount": ..., "contextWindow": ... } }
  { "type": "text-delta", "chatId": "...", "data": "chunk" }
  { "type": "thinking-delta", "chatId": "...", "data": "chunk" }
  { "type": "tool-call", "chatId": "...", "data": { "toolCallId", "toolName", "args", "argsText" } }
  { "type": "done", "chatId": "..." }
  { "type": "interrupted", "chatId": "..." }
  { "type": "context-update", "chatId": "...", "data": { "tokenCount": 12345, "tokenLimit": 200000 } }
  { "type": "error", "chatId": "...", "data": "something broke" }
"""

import json
from datetime import datetime, timezone

import logfire
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from alpha_sdk import AssistantEvent, ResultEvent, ErrorEvent, StreamEvent

from alpha_app.chat import MODEL, Chat, ChatState, Holster, generate_chat_id
from alpha_app.db import get_pool

router = APIRouter()


# -- Logfire span helpers -----------------------------------------------------


def _build_prompt_preview(content: list[dict], max_len: int = 50) -> str:
    """Extract a short preview from content blocks for span naming."""
    for block in content:
        if block.get("type") == "text":
            text = block.get("text", "")
            if text:
                return text[:max_len] + ("…" if len(text) > max_len else "")
    return "(no text)"


def _format_input_messages(content: list[dict]) -> list[dict]:
    """Format content blocks as gen_ai.input.messages for Logfire Model Run card.

    Logfire expects: [{"role": "user", "parts": [{"type": "text", "content": "..."}]}]
    We receive Messages API blocks: [{"type": "text", "text": "..."}, ...]
    """
    parts = []
    for block in content:
        block_type = block.get("type", "")
        if block_type == "text":
            parts.append({"type": "text", "content": block.get("text", "")})
        elif block_type == "image":
            media = block.get("source", {}).get("media_type", "image")
            parts.append({"type": "image", "content": f"({media})"})
        else:
            parts.append({"type": block_type, "content": f"({block_type})"})
    return [{"role": "user", "parts": parts}]


def _format_output_messages(output_parts: list[dict]) -> list[dict]:
    """Format assistant content blocks as gen_ai.output.messages for Logfire.

    Tool calls use Logfire's ToolCallPart format (type="tool_call" with id/name/arguments),
    NOT Anthropic's raw tool_use format. This is what triggers proper rendering in the
    Model Run card.
    """
    parts = []
    for block in output_parts:
        block_type = block.get("type", "")
        if block_type == "text":
            parts.append({"type": "text", "content": block.get("text", "")})
        elif block_type == "tool_use":
            # Logfire's normalized tool_call format (from semconv.py ToolCallPart)
            parts.append({
                "type": "tool_call",
                "id": block.get("id", ""),
                "name": block.get("name", ""),
                "arguments": block.get("input"),
            })
    return [{"role": "assistant", "parts": parts}]


# -- Postgres helpers ---------------------------------------------------------


async def _persist_chat(chat: Chat) -> None:
    """Persist chat metadata to Postgres. Upsert by chat ID."""
    try:
        pool = get_pool()
        updated = datetime.fromtimestamp(chat.updated_at, tz=timezone.utc)
        await pool.execute(
            """
            INSERT INTO app.chats (id, updated_at, data)
            VALUES ($1, $2, $3)
            ON CONFLICT (id) DO UPDATE
                SET updated_at = EXCLUDED.updated_at,
                    data = EXCLUDED.data
            """,
            chat.id,
            updated,
            chat.to_data(),
        )
    except Exception:
        pass  # Non-fatal


async def _list_chats() -> list[dict]:
    """Load chat list from Postgres for sidebar hydration."""
    try:
        pool = get_pool()
        rows = await pool.fetch(
            """
            SELECT id, updated_at, data
            FROM app.chats
            ORDER BY updated_at DESC
            LIMIT 100
            """
        )
        result = []
        for row in rows:
            data = row["data"]
            result.append({
                "chatId": row["id"],
                "title": data.get("title", ""),
                "state": "dead",  # Always dead from DB — live overlay adds runtime state
                "updatedAt": row["updated_at"].timestamp(),
                "sessionUuid": data.get("session_uuid", ""),
                "tokenCount": data.get("token_count", 0) or 0,
                "contextWindow": data.get("context_window", 0) or 200_000,
            })
        return result
    except Exception:
        pass  # Non-fatal
        return []


async def _load_chat(chat_id: str) -> Chat | None:
    """Load a chat's metadata from Postgres. Returns a DEAD Chat, or None."""
    try:
        pool = get_pool()
        row = await pool.fetchrow(
            "SELECT id, updated_at, data FROM app.chats WHERE id = $1",
            chat_id,
        )
        if row:
            return Chat.from_db(
                chat_id=row["id"],
                updated_at=row["updated_at"].timestamp(),
                data=row["data"],
            )
        return None
    except Exception:
        pass  # Non-fatal
        return None


# -- Event streaming ----------------------------------------------------------


def _set_turn_span_response(span, chat: Chat, result: ResultEvent, output_parts: list) -> None:
    """Set gen_ai response attributes on the turn span.

    Follows the attribute schema that triggers Logfire's Model Run card.
    Matches the proven format from Rosemary's turn spans.
    """
    # Model Run card attributes (these trigger the card)
    span.set_attribute("gen_ai.response.model", chat.response_model or "")
    span.set_attribute("gen_ai.usage.input_tokens", chat.input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", chat.output_tokens)
    span.set_attribute("gen_ai.usage.cache_creation.input_tokens", chat.cache_creation_tokens)
    span.set_attribute("gen_ai.usage.cache_read.input_tokens", chat.cache_read_tokens)

    output_messages = _format_output_messages(output_parts)
    span.set_attribute("gen_ai.output.messages", output_messages)

    # Custom extras (don't affect Model Run card, useful for debugging)
    span.set_attribute("gen_ai.response.id", chat.response_id or "")
    span.set_attribute("gen_ai.response.finish_reasons", [chat.stop_reason or "unknown"])
    span.set_attribute("gen_ai.token_count", chat.token_count)
    span.set_attribute("cost_usd", result.cost_usd)
    span.set_attribute("duration_ms", result.duration_ms)
    span.set_attribute("inference_count", result.num_turns)
    span.set_attribute("response_length", sum(
        len(p.get("content", ""))
        for msg in output_messages
        for p in msg.get("parts", [])
    ))

    # Quota utilization
    if chat.usage_5h is not None:
        span.set_attribute("anthropic.quota.usage_5h", chat.usage_5h)
    if chat.usage_7d is not None:
        span.set_attribute("anthropic.quota.usage_7d", chat.usage_7d)


async def _stream_chat_events(ws: WebSocket, chat: Chat, span=None) -> None:
    """Stream events from a Chat to the WebSocket. Handles turn lifecycle.

    All emitted events carry the chatId. On turn completion, emits chat-state
    with the updated state, then done.

    Also monitors token_count changes (from the proxy's SSE sniffing) and
    emits context-update events in real time — so the browser ContextMeter
    updates live during multi-tool-call turns.

    If span is provided, sets gen_ai response attributes on it at turn end.
    """
    chat_id = chat.id
    turn_completed = False
    last_token_count = chat.token_count  # Track changes
    output_parts: list[dict] = []  # Accumulate assistant content for span

    try:
        async for event in chat.events():
            # -- Real-time context updates --
            # Check after each event whether token_count changed.
            # The proxy sniffs input_tokens from each API call's message_start,
            # so during a 30-tool-call turn the count climbs. After auto-compact,
            # it drops. Either way, the browser sees it live.
            current_tokens = chat.token_count
            if current_tokens != last_token_count:
                last_token_count = current_tokens
                try:
                    await ws.send_json({
                        "type": "context-update",
                        "chatId": chat_id,
                        "data": {
                            "tokenCount": current_tokens,
                            "tokenLimit": chat.context_window,
                        },
                    })
                except Exception:
                    pass  # Don't let meter updates kill the stream

            if isinstance(event, StreamEvent):
                if event.delta_type == "text_delta":
                    text = event.delta_text
                    if text:
                        await ws.send_json({"type": "text-delta", "chatId": chat_id, "data": text})
                elif event.delta_type == "thinking_delta":
                    text = event.delta_text
                    if text:
                        await ws.send_json({"type": "thinking-delta", "chatId": chat_id, "data": text})

            elif isinstance(event, AssistantEvent):
                output_parts.extend(event.content)
                for block in event.content:
                    if block.get("type") == "tool_use":
                        await ws.send_json({
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
                # Persist updated metadata (session UUID, title, etc.)
                await _persist_chat(chat)

                # Set gen_ai attributes on the turn span (before any cleanup)
                if span:
                    _set_turn_span_response(span, chat, event, output_parts)

                # Notify state change: BUSY -> IDLE
                await ws.send_json({
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
                await ws.send_json({"type": "error", "chatId": chat_id, "data": event.message})

    except Exception as e:
        if span:
            span.set_attribute("error.type", type(e).__name__)
        try:
            await ws.send_json({"type": "error", "chatId": chat_id, "data": str(e)})
        except Exception:
            pass

    finally:
        # If the turn didn't complete normally, the chat might be stuck in BUSY.
        # Reap it so it doesn't block future sends.
        if not turn_completed and chat.state == ChatState.BUSY:
            await chat.reap()

    # Signal turn complete
    try:
        await ws.send_json({"type": "done", "chatId": chat_id})
    except Exception:
        pass


# -- Message handlers ---------------------------------------------------------


async def _handle_create_chat(
    ws: WebSocket,
    holster: Holster,
    chats: dict[str, Chat],
) -> None:
    """Handle create-chat: claim from holster, persist, return chatId."""
    try:
        claude = await holster.claim()
        chat_id = generate_chat_id()
        system_prompt = ws.app.state.system_prompt
        chat = Chat.from_holster(id=chat_id, claude=claude, system_prompt=system_prompt)
        chats[chat_id] = chat

        # Persist to Postgres
        await _persist_chat(chat)

        await ws.send_json({
            "type": "chat-created",
            "chatId": chat_id,
            "data": {"state": chat.state.value},
        })

    except Exception as e:
        await ws.send_json({"type": "error", "data": f"Failed to create chat: {e}"})


async def _handle_list_chats(
    ws: WebSocket,
    chats: dict[str, Chat],
) -> None:
    """Handle list-chats: return chat metadata from Postgres, overlaid with live state."""
    chat_list = await _list_chats()

    # Overlay runtime state from live in-memory chats.
    # Postgres always reports "dead" — only live chats have real state.
    for item in chat_list:
        live_chat = chats.get(item["chatId"])
        if live_chat:
            item["state"] = live_chat.state.value
            item["title"] = live_chat.title or item["title"]
            item["sessionUuid"] = live_chat.session_uuid or item.get("sessionUuid", "")
            item["tokenCount"] = live_chat.token_count
            item["contextWindow"] = live_chat.context_window

    await ws.send_json({"type": "chat-list", "data": chat_list})


async def _handle_send(
    ws: WebSocket,
    chats: dict[str, Chat],
    holster: Holster,
    chat_id: str,
    raw_content: str | list,
) -> None:
    """Handle send: route message to the right chat, stream response."""
    if not chat_id:
        await ws.send_json({"type": "error", "data": "Missing chatId"})
        return

    # Find or load the chat
    chat = chats.get(chat_id)
    if not chat:
        chat = await _load_chat(chat_id)
        if chat:
            chats[chat_id] = chat
        else:
            await ws.send_json({"type": "error", "chatId": chat_id, "data": "Chat not found"})
            await ws.send_json({"type": "done", "chatId": chat_id})
            return

    # Normalize content to content blocks
    if isinstance(raw_content, str):
        content: list[dict] = [{"type": "text", "text": raw_content}]
    elif isinstance(raw_content, list):
        content = raw_content
    else:
        content = [{"type": "text", "text": str(raw_content)}]

    prompt_preview = _build_prompt_preview(content)

    with logfire.span(
        "alpha.turn: {prompt_preview}",
        **{
            "prompt_preview": prompt_preview,
            # Model Run card attributes (match Rosemary's proven schema)
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "anthropic",
            "gen_ai.request.model": MODEL,
            "gen_ai.system_instructions": [{"type": "text", "content": ws.app.state.system_prompt}],
            "gen_ai.input.messages": _format_input_messages(content),
            # Custom extras
            "client_name": "alpha",
            "chat.id": chat_id,
            "session_id": chat.session_uuid or "",
        },
    ) as span:
        try:
            # Resurrect if DEAD
            resurrected = False
            if chat.state == ChatState.DEAD:
                if not chat.session_uuid:
                    await ws.send_json({
                        "type": "error",
                        "chatId": chat_id,
                        "data": "Chat is dead with no session to resume",
                    })
                    await ws.send_json({"type": "done", "chatId": chat_id})
                    return

                await ws.send_json({
                    "type": "chat-state",
                    "chatId": chat_id,
                    "data": {
                        "state": "starting",
                        "sessionUuid": chat.session_uuid or "",
                        "tokenCount": chat.token_count,
                        "contextWindow": chat.context_window,
                    },
                })
                await chat.resurrect(system_prompt=ws.app.state.system_prompt)
                resurrected = True
                await ws.send_json({
                    "type": "chat-state",
                    "chatId": chat_id,
                    "data": {
                        "state": chat.state.value,
                        "sessionUuid": chat.session_uuid or "",
                        "tokenCount": chat.token_count,
                        "contextWindow": chat.context_window,
                    },
                })

            # Propagate trace context to proxy so its spans nest under this turn
            chat.set_trace_context(logfire.get_context())

            # Send the message (IDLE -> BUSY, sets title + updated_at)
            await chat.send(content)

            span.set_attribute("chat.title", chat.title)
            span.set_attribute("chat.resurrected", resurrected)

            # Notify state change: IDLE -> BUSY
            await ws.send_json({
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

            # Persist updated metadata
            await _persist_chat(chat)

            # Stream events (ends with chat-state IDLE + done)
            await _stream_chat_events(ws, chat, span)

        except Exception as e:
            span.set_attribute("error.type", type(e).__name__)
            await ws.send_json({"type": "error", "chatId": chat_id, "data": str(e)})
            await ws.send_json({"type": "done", "chatId": chat_id})


async def _handle_interrupt(
    ws: WebSocket,
    chats: dict[str, Chat],
    chat_id: str,
) -> None:
    """Handle interrupt: cancel the active turn for a specific chat."""
    if not chat_id:
        await ws.send_json({"type": "error", "data": "Missing chatId"})
        return

    chat = chats.get(chat_id)
    if chat:
        try:
            await chat.interrupt()
            await ws.send_json({
                "type": "chat-state",
                "chatId": chat_id,
                "data": {
                    "state": chat.state.value,
                    "sessionUuid": chat.session_uuid or "",
                    "tokenCount": chat.token_count,
                    "contextWindow": chat.context_window,
                },
            })
        except Exception as e:
            await ws.send_json({"type": "error", "chatId": chat_id, "data": str(e)})

    await ws.send_json({"type": "interrupted", "chatId": chat_id})


# -- WebSocket handler --------------------------------------------------------


@router.websocket("/ws")
async def websocket_chat(ws: WebSocket) -> None:
    """Bidirectional multi-chat over WebSocket.

    Phase 2: All messages carry chatId. Multiple chats multiplexed
    over one connection. The create-chat round-trip is instant thanks
    to the Holster.
    """
    await ws.accept()
    holster: Holster = ws.app.state.holster
    chats: dict[str, Chat] = ws.app.state.chats

    try:
        while True:
            raw = await ws.receive_json()
            msg_type = raw.get("type")

            if msg_type == "create-chat":
                await _handle_create_chat(ws, holster, chats)

            elif msg_type == "list-chats":
                await _handle_list_chats(ws, chats)

            elif msg_type == "send":
                chat_id = raw.get("chatId", "")
                raw_content = raw.get("content", "")
                await _handle_send(ws, chats, holster, chat_id, raw_content)

            elif msg_type == "interrupt":
                chat_id = raw.get("chatId", "")
                await _handle_interrupt(ws, chats, chat_id)

            else:
                pass

    except WebSocketDisconnect:
        pass
    except Exception:
        pass

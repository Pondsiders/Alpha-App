"""WebSocket route — bidirectional multi-chat switch.

Phase 3: The Switch — all events broadcast to all connected clients.

Every WebSocket connection registers in app.state.connections (a set).
Streaming events, state changes, and user message echoes broadcast to all
connections. Request/response messages (list-chats) unicast to the requester.
The reap timer broadcasts DEAD state via on_reap callback.

Two tabs = two connections = same conversation, synced.

Client -> Server messages:
  { "type": "create-chat" }
  { "type": "list-chats" }
  { "type": "send", "chatId": "...", "content": "Hello" }
  { "type": "send", "chatId": "...", "content": [{ "type": "text", "text": "Hello" }, ...] }
  { "type": "interrupt", "chatId": "..." }

Server -> Client messages (unicast — response to requester only):
  { "type": "chat-list", "data": [...] }

Server -> Client messages (broadcast — all connections):
  { "type": "chat-created", "chatId": "...", "data": { "state": "idle" } }
  { "type": "chat-state", "chatId": "...", "data": { "state": "busy", "title": "...", ... } }
  { "type": "user-message", "chatId": "...", "data": { "content": [...] } }
  { "type": "text-delta", "chatId": "...", "data": "chunk" }
  { "type": "thinking-delta", "chatId": "...", "data": "chunk" }
  { "type": "tool-call", "chatId": "...", "data": { "toolCallId", "toolName", "args", "argsText" } }
  { "type": "done", "chatId": "..." }
  { "type": "interrupted", "chatId": "..." }
  { "type": "context-update", "chatId": "...", "data": { "tokenCount": 12345, "tokenLimit": 200000 } }
  { "type": "error", "chatId": "...", "data": "something broke" }
"""

import asyncio
import json
from datetime import datetime, timezone

import logfire
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from alpha_sdk import AssistantEvent, ResultEvent, ErrorEvent, StreamEvent

from alpha_app.chat import MODEL, Chat, ChatState, Holster, generate_chat_id
from alpha_app.db import get_pool

router = APIRouter()


# -- Broadcast ----------------------------------------------------------------
# The heart of the switch. Send an event to every connected client.
# Unicast (ws.send_json) is for request/response. Broadcast is for events.


async def _broadcast(
    connections: set,
    event: dict,
    *,
    exclude: WebSocket | None = None,
) -> None:
    """Send event to all connected WebSockets, optionally excluding one.

    Dead connections (send fails) are silently removed from the set.
    Uses asyncio.gather for parallel delivery.
    """
    targets = [c for c in connections if c is not exclude]
    if not targets:
        return
    results = await asyncio.gather(
        *(c.send_json(event) for c in targets),
        return_exceptions=True,
    )
    for conn, result in zip(targets, results):
        if isinstance(result, Exception):
            connections.discard(conn)


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
            source = block.get("source", {})
            if source.get("type") == "base64":
                data = source.get("data", "")
                media_type = source.get("media_type", "image/jpeg")
                data_uri = f"data:{media_type};base64,{data}"
                parts.append({
                    "type": "uri",
                    "content": data_uri,
                    "media_type": media_type,
                })
            else:
                media = source.get("media_type", "image")
                parts.append({"type": "image", "content": f"({media})"})
        else:
            parts.append({"type": block_type, "content": f"({block_type})"})
    return [{"role": "user", "parts": parts}]


def _format_output_messages(output_parts: list[dict]) -> list[dict]:
    """Format assistant content blocks as gen_ai.output.messages for Logfire."""
    parts = []
    for block in output_parts:
        block_type = block.get("type", "")
        if block_type == "text":
            parts.append({"type": "text", "content": block.get("text", "")})
        elif block_type == "tool_use":
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
                "state": "dead",
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
    """Set gen_ai response attributes on the turn span."""
    span.set_attribute("gen_ai.response.model", chat.response_model or "")
    span.set_attribute("gen_ai.usage.input_tokens", chat.total_input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", chat.output_tokens)
    span.set_attribute("gen_ai.usage.cache_creation.input_tokens", chat.cache_creation_tokens)
    span.set_attribute("gen_ai.usage.cache_read.input_tokens", chat.cache_read_tokens)

    output_messages = _format_output_messages(output_parts)
    span.set_attribute("gen_ai.output.messages", output_messages)

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

    if chat.usage_5h is not None:
        span.set_attribute("anthropic.quota.usage_5h", chat.usage_5h)
    if chat.usage_7d is not None:
        span.set_attribute("anthropic.quota.usage_7d", chat.usage_7d)


async def _stream_chat_events(connections: set, chat: Chat, span=None) -> None:
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
                    await _broadcast(connections, {
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
                        await _broadcast(connections, {"type": "text-delta", "chatId": chat_id, "data": text})
                elif event.delta_type == "thinking_delta":
                    text = event.delta_text
                    if text:
                        await _broadcast(connections, {"type": "thinking-delta", "chatId": chat_id, "data": text})

            elif isinstance(event, AssistantEvent):
                output_parts.extend(event.content)
                for block in event.content:
                    if block.get("type") == "tool_use":
                        await _broadcast(connections, {
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
                await _persist_chat(chat)

                if span:
                    _set_turn_span_response(span, chat, event, output_parts)

                await _broadcast(connections, {
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
                await _broadcast(connections, {"type": "error", "chatId": chat_id, "data": event.message})

    except Exception as e:
        if span:
            span.set_attribute("error.type", type(e).__name__)
        try:
            await _broadcast(connections, {"type": "error", "chatId": chat_id, "data": str(e)})
        except Exception:
            pass

    finally:
        if not turn_completed and chat.state == ChatState.BUSY:
            await chat.reap()

    try:
        await _broadcast(connections, {"type": "done", "chatId": chat_id})
    except Exception:
        pass


# -- Message handlers ---------------------------------------------------------


async def _handle_create_chat(
    ws: WebSocket,
    connections: set,
    holster: Holster,
    chats: dict[str, Chat],
    on_reap,
) -> None:
    """Handle create-chat: claim from holster, persist, broadcast to all."""
    try:
        claude = await holster.claim()
        chat_id = generate_chat_id()
        system_prompt = ws.app.state.system_prompt
        chat = Chat.from_holster(id=chat_id, claude=claude, system_prompt=system_prompt)
        chat.on_reap = on_reap
        chats[chat_id] = chat

        await _persist_chat(chat)

        # Broadcast to all — the requester navigates (createPendingRef),
        # other tabs just add it to their sidebar.
        await _broadcast(connections, {
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
    """Handle list-chats: unicast — only the requester needs the full list."""
    chat_list = await _list_chats()

    for item in chat_list:
        live_chat = chats.get(item["chatId"])
        if live_chat:
            item["state"] = live_chat.state.value
            item["title"] = live_chat.title or item["title"]
            item["sessionUuid"] = live_chat.session_uuid or item.get("sessionUuid", "")
            item["tokenCount"] = live_chat.token_count
            item["contextWindow"] = live_chat.context_window

    await ws.send_json({"type": "chat-list", "data": chat_list})


def _normalize_content(raw_content: str | list) -> list[dict]:
    """Normalize raw content to Messages API content blocks."""
    if isinstance(raw_content, str):
        return [{"type": "text", "text": raw_content}]
    elif isinstance(raw_content, list):
        return raw_content
    else:
        return [{"type": "text", "text": str(raw_content)}]


async def _handle_new_turn(
    ws: WebSocket,
    connections: set,
    chat: Chat,
    content: list[dict],
    turn_input_messages: dict[str, list],
    streaming_tasks: dict[str, asyncio.Task],
) -> None:
    """Handle a new turn: resurrect if needed, send, stream events.

    Runs as a background task so the WebSocket read loop stays hot.
    State changes and streaming events broadcast to ALL connections.
    """
    chat_id = chat.id
    prompt_preview = _build_prompt_preview(content)

    input_messages = _format_input_messages(content)
    turn_input_messages[chat_id] = input_messages

    with logfire.span(
        "alpha.turn: {prompt_preview}",
        **{
            "prompt_preview": prompt_preview,
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "anthropic",
            "gen_ai.provider.name": "anthropic",
            "gen_ai.request.model": MODEL,
            "gen_ai.conversation.id": chat_id,
            "gen_ai.system_instructions": [{"type": "text", "content": ws.app.state.system_prompt}],
            "gen_ai.input.messages": input_messages,
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
                    await _broadcast(connections, {
                        "type": "error",
                        "chatId": chat_id,
                        "data": "Chat is dead with no session to resume",
                    })
                    await _broadcast(connections, {"type": "done", "chatId": chat_id})
                    return

                await _broadcast(connections, {
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
                await _broadcast(connections, {
                    "type": "chat-state",
                    "chatId": chat_id,
                    "data": {
                        "state": chat.state.value,
                        "sessionUuid": chat.session_uuid or "",
                        "tokenCount": chat.token_count,
                        "contextWindow": chat.context_window,
                    },
                })

            chat.set_trace_context(logfire.get_context())

            await chat.send(content)

            span.set_attribute("chat.title", chat.title)
            span.set_attribute("chat.resurrected", resurrected)

            # Notify state change: IDLE -> BUSY (all clients)
            await _broadcast(connections, {
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

            await _persist_chat(chat)

            # Stream events to all connections
            await _stream_chat_events(connections, chat, span)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            span.set_attribute("error.type", type(e).__name__)
            try:
                await _broadcast(connections, {"type": "error", "chatId": chat_id, "data": str(e)})
                await _broadcast(connections, {"type": "done", "chatId": chat_id})
            except Exception:
                pass
        finally:
            final_messages = turn_input_messages.get(chat_id)
            if final_messages and len(final_messages) > len(input_messages):
                span.set_attribute("gen_ai.input.messages", final_messages)

            turn_input_messages.pop(chat_id, None)
            streaming_tasks.pop(chat_id, None)


async def _handle_interjection(
    ws: WebSocket,
    connections: set,
    chat: Chat,
    content: list[dict],
    turn_input_messages: dict[str, list],
) -> None:
    """Handle an interjection: feed to BUSY subprocess, echo to other clients."""
    await chat.send(content)

    # Echo user message to other connections (sender has it optimistically)
    await _broadcast(connections, {
        "type": "user-message",
        "chatId": chat.id,
        "data": {"content": content},
    }, exclude=ws)

    # Append to the turn's input messages for the Logfire span
    if chat.id in turn_input_messages:
        turn_input_messages[chat.id].extend(_format_input_messages(content))


async def _handle_interrupt(
    ws: WebSocket,
    connections: set,
    chats: dict[str, Chat],
    chat_id: str,
    streaming_tasks: dict[str, asyncio.Task],
) -> None:
    """Handle interrupt: kill the subprocess, broadcast state change."""
    if not chat_id:
        await ws.send_json({"type": "error", "data": "Missing chatId"})
        return

    chat = chats.get(chat_id)
    if chat:
        try:
            await chat.interrupt()
            await _broadcast(connections, {
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
            await _broadcast(connections, {"type": "error", "chatId": chat_id, "data": str(e)})

    task = streaming_tasks.get(chat_id)
    if task and not task.done():
        task.cancel()

    await _broadcast(connections, {"type": "interrupted", "chatId": chat_id})


# -- WebSocket handler --------------------------------------------------------


@router.websocket("/ws")
async def websocket_chat(ws: WebSocket) -> None:
    """Bidirectional multi-chat switch.

    Full duplex: the read loop stays hot while streaming tasks run in the
    background. All events broadcast to all connected clients. Sends to a
    BUSY chat become interjections. User messages echo to other connections.

    Two tabs, same conversation, fully synced.
    """
    await ws.accept()

    # Register in the connection set (the switch fabric)
    connections: set = ws.app.state.connections
    connections.add(ws)

    holster: Holster = ws.app.state.holster
    chats: dict[str, Chat] = ws.app.state.chats

    # Reap callback — when a chat's idle timer fires, broadcast DEAD to all.
    async def on_chat_reap(chat_id: str) -> None:
        await _broadcast(connections, {
            "type": "chat-state",
            "chatId": chat_id,
            "data": {"state": "dead"},
        })

    # Per-connection tracking for background streaming tasks
    streaming_tasks: dict[str, asyncio.Task] = {}
    turn_input_messages: dict[str, list] = {}

    try:
        while True:
            raw = await ws.receive_json()
            msg_type = raw.get("type")

            if msg_type == "create-chat":
                await _handle_create_chat(ws, connections, holster, chats, on_chat_reap)

            elif msg_type == "list-chats":
                await _handle_list_chats(ws, chats)

            elif msg_type == "send":
                chat_id = raw.get("chatId", "")
                raw_content = raw.get("content", "")

                if not chat_id:
                    await ws.send_json({"type": "error", "data": "Missing chatId"})
                    continue

                content = _normalize_content(raw_content)

                # Find or load the chat
                chat = chats.get(chat_id)
                if not chat:
                    chat = await _load_chat(chat_id)
                    if chat:
                        chat.on_reap = on_chat_reap
                        chats[chat_id] = chat
                    else:
                        await ws.send_json({"type": "error", "chatId": chat_id, "data": "Chat not found"})
                        await ws.send_json({"type": "done", "chatId": chat_id})
                        continue

                if chat.state == ChatState.BUSY:
                    # Interjection — feed to subprocess, echo to others
                    await _handle_interjection(ws, connections, chat, content, turn_input_messages)
                else:
                    # Echo user message to other connections
                    await _broadcast(connections, {
                        "type": "user-message",
                        "chatId": chat_id,
                        "data": {"content": content},
                    }, exclude=ws)

                    # New turn — start streaming in background
                    task = asyncio.create_task(
                        _handle_new_turn(ws, connections, chat, content, turn_input_messages, streaming_tasks)
                    )
                    streaming_tasks[chat_id] = task

            elif msg_type == "interrupt":
                chat_id = raw.get("chatId", "")
                await _handle_interrupt(ws, connections, chats, chat_id, streaming_tasks)

            else:
                pass

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        # Deregister from the connection set
        connections.discard(ws)

        # Cancel all streaming tasks on disconnect
        for task in streaming_tasks.values():
            if not task.done():
                task.cancel()
        for task in list(streaming_tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

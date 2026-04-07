"""WebSocket route — command/event protocol.

See PROTOCOL.md for the wire format. Summary:
- Client sends commands: { "command": "join-chat", "id": "req_1", "chatId": "xyz" }
- Server sends events:   { "event": "text-delta", "chatId": "xyz", "delta": "Hello" }
- Commands with `id` get a correlated response event (or error).
- Commands without `id` are fire-and-forget.

All events broadcast to all connected clients (multi-tab sync).
"""

import asyncio
from typing import Any

import logfire
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from alpha_app.chat import MODEL, Chat, ConversationState
from alpha_app.db import load_chat, load_messages
from alpha_app.protocol import (
    AppStateEvent,
    BuzzCommand,
    ChatCreatedEvent,
    ChatLoadedEvent,
    ChatStateEvent,
    CreateChatCommand,
    ErrorEvent,
    InterruptCommand,
    JoinChatCommand,
    SendAckEvent,
    SendCommand,
    parse_command,
)
from alpha_app.routes.broadcast import broadcast
from alpha_app.routes.enrobe import enrobe
from alpha_app.routes.handlers import handle_create_chat, handle_interrupt
from alpha_app.routes.spans import build_prompt_preview, format_input_messages
from alpha_app.strings import BUZZ_NARRATION
from alpha_app.tools import create_alpha_server

router = APIRouter()


# =============================================================================
# Shared state container (passed to every handler)
# =============================================================================

class WsContext:
    """Per-connection context passed to command handlers."""

    def __init__(self, ws: WebSocket, connections: set, chats: dict[str, Chat]):
        self.ws = ws
        self.connections = connections
        self.chats = chats
        self.streaming_tasks: dict[str, asyncio.Task] = {}

    async def send_event(self, event_model) -> None:
        """Send a typed event to THIS connection (unicast)."""
        await self.ws.send_json(event_model.model_dump(exclude_none=True))

    async def broadcast_event(self, event_dict: dict) -> None:
        """Broadcast a raw event dict to ALL connections."""
        await broadcast(self.connections, event_dict)

    async def send_error(self, code: str, message: str, *, id: str | None = None, chatId: str | None = None) -> None:
        """Send an error event."""
        await self.send_event(ErrorEvent(
            event="error", id=id, chatId=chatId, code=code, message=message,
        ))

    def wire_chat(self, chat: Chat) -> None:
        """Set callbacks and registry on a Chat."""
        async def on_reap(chat_id: str) -> None:
            await self.broadcast_event(
                ChatStateEvent(event="chat-state", chatId=chat_id, state="dead").model_dump(exclude_none=True)
            )

        async def on_broadcast(event: dict) -> None:
            await self.broadcast_event(event)

        chat.on_reap = on_reap
        chat.on_broadcast = on_broadcast
        chat._topic_registry = getattr(self.ws.app.state, "topic_registry", None)

    async def find_or_load_chat(self, chat_id: str) -> Chat | None:
        """Find a chat in memory or load from Postgres."""
        chat = self.chats.get(chat_id)
        if not chat:
            chat = await load_chat(chat_id)
            if chat:
                self.wire_chat(chat)
                self.chats[chat_id] = chat
        else:
            self.wire_chat(chat)
        return chat


# =============================================================================
# Command handlers
# =============================================================================

async def cmd_join_chat(ctx: WsContext, cmd: JoinChatCommand) -> None:
    """Handle join-chat: load full history + metadata."""
    chat = await ctx.find_or_load_chat(cmd.chatId)

    if not chat:
        await ctx.send_error("not-found", f"Chat {cmd.chatId} not found", id=cmd.id, chatId=cmd.chatId)
        return

    # Load messages: prefer in-memory (includes in-progress deltas), fall back to Postgres.
    in_memory = cmd.chatId in ctx.chats
    if in_memory:
        messages = chat.messages_to_wire()
    else:
        messages = await load_messages(cmd.chatId)

    # Build metadata from wire_state
    ws = chat.wire_state()

    await ctx.send_event(ChatLoadedEvent(
        event="chat-loaded",
        id=cmd.id,
        chatId=cmd.chatId,
        title=ws.get("title", ""),
        createdAt=ws.get("createdAt", 0),
        updatedAt=ws.get("updatedAt", 0),
        state=ws.get("state", "dead"),
        tokenCount=ws.get("tokenCount", 0),
        contextWindow=ws.get("contextWindow", 1_000_000),
        messages=messages,
    ))

    # Eager warmup: if COLD with a session, start the subprocess now.
    if (
        chat.state == ConversationState.COLD
        and chat.session_uuid is not None
    ):
        chat._system_prompt = await ctx.ws.app.state.get_system_prompt()
        chat._topic_registry = getattr(ctx.ws.app.state, "topic_registry", None)
        try:
            await chat._ensure_claude()
            await ctx.broadcast_event(
                ChatStateEvent(event="chat-state", chatId=cmd.chatId, state=chat.state.wire_value).model_dump(exclude_none=True)
            )
        except Exception as exc:
            logfire.warn("eager-warmup failed: {error}", error=str(exc))


async def cmd_create_chat(ctx: WsContext, cmd: CreateChatCommand) -> None:
    """Handle create-chat: create a new conversation."""
    # Reuse existing handler (it does the nanoid generation, wiring, etc.)
    await handle_create_chat(ctx.ws, ctx.connections, ctx.chats,
                              ctx.wire_chat.__func__.__get__(ctx),  # pass bound method
                              ctx.broadcast_event)
    # TODO: refactor handle_create_chat to return the chat so we can send
    # a proper ChatCreatedEvent with the id field for correlation.


async def cmd_send(ctx: WsContext, cmd: SendCommand) -> None:
    """Handle send: deliver a user message to Claude."""
    chat = await ctx.find_or_load_chat(cmd.chatId)
    if not chat:
        await ctx.send_error("not-found", f"Chat {cmd.chatId} not found", id=cmd.id, chatId=cmd.chatId)
        return

    # Ack immediately
    await ctx.send_event(SendAckEvent(event="send-ack", id=cmd.id, chatId=cmd.chatId))

    # If a turn is active, this is a steering message (interjection).
    if chat._active_turn:
        await chat._active_turn.send(cmd.content)
    else:
        task = asyncio.create_task(
            _run_human_turn(ctx, chat, cmd.content, source="human")
        )
        ctx.streaming_tasks[cmd.chatId] = task


async def cmd_buzz(ctx: WsContext, cmd: BuzzCommand) -> None:
    """Handle buzz: the duck button."""
    chat = await ctx.find_or_load_chat(cmd.chatId)
    if not chat:
        await ctx.send_error("not-found", f"Chat {cmd.chatId} not found", id=cmd.id, chatId=cmd.chatId)
        return

    narration = [{"type": "text", "text": BUZZ_NARRATION}]
    task = asyncio.create_task(
        _run_human_turn(ctx, chat, narration, broadcast_user_message=False, source="buzzer")
    )
    ctx.streaming_tasks[cmd.chatId] = task


async def cmd_interrupt(ctx: WsContext, cmd: InterruptCommand) -> None:
    """Handle interrupt: stop Claude mid-response."""
    await handle_interrupt(ctx.ws, ctx.connections, ctx.chats, cmd.chatId, ctx.streaming_tasks)


# =============================================================================
# Command dispatch registry
# =============================================================================

DISPATCH = {
    "join-chat": cmd_join_chat,
    "create-chat": cmd_create_chat,
    "send": cmd_send,
    "buzz": cmd_buzz,
    "interrupt": cmd_interrupt,
}


# =============================================================================
# Turn execution (extracted from the old _run_human_turn)
# =============================================================================

def _normalize_content(raw_content: str | list) -> list[dict]:
    """Normalize raw content to Messages API content blocks."""
    if isinstance(raw_content, str):
        return [{"type": "text", "text": raw_content}]
    elif isinstance(raw_content, list):
        return raw_content
    else:
        return [{"type": "text", "text": str(raw_content)}]


async def _run_human_turn(
    ctx: WsContext,
    chat: Chat,
    content: list[dict],
    *,
    broadcast_user_message: bool = True,
    source: str = "human",
    msg_id: str | None = None,
    topics: list[str] | None = None,
) -> None:
    """Handle a human send: enrobe, send via turn lock.

    CHAT-V2: Claude auto-starts if cold (via _ensure_claude in Turn.send).
    No explicit resurrect. Fire-and-forget from the WebSocket handler.
    """
    chat_id = chat.id
    connections = ctx.connections
    prompt_preview = build_prompt_preview(content)

    chat._topic_registry = getattr(ctx.ws.app.state, "topic_registry", None)

    # Open Logfire span
    span = logfire.span(
        "alpha.turn: {prompt_preview}",
        **{
            "prompt_preview": prompt_preview,
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "anthropic",
            "gen_ai.provider.name": "anthropic",
            "gen_ai.request.model": MODEL,
            "gen_ai.conversation.id": chat_id,
            "gen_ai.input.messages": format_input_messages(content),
            "client_name": "alpha",
            "chat.id": chat_id,
            "session_id": chat.session_uuid or "",
        },
    )
    span.__enter__()
    chat._turn_span = span

    try:
        chat.set_trace_context(logfire.get_context())

        async def _enrobe_broadcast(event: dict) -> None:
            if broadcast_user_message:
                await broadcast(connections, event)

        topic_registry = getattr(ctx.ws.app.state, "topic_registry", None)
        result = await enrobe(
            content, chat=chat, source=source, msg_id=msg_id,
            topics=topics, topic_registry=topic_registry,
            broadcast_fn=_enrobe_broadcast,
        )

        enriched_messages = format_input_messages(result.content)
        span.set_attribute("gen_ai.input.messages", enriched_messages)

        async with await chat.turn() as t:
            await t.send(result.message)

        await broadcast(connections, {
            "type": "chat-state",
            "chatId": chat_id,
            "data": chat.wire_state(),
        })

    except asyncio.CancelledError:
        if chat._turn_span:
            chat._turn_span.__exit__(None, None, None)
            chat._turn_span = None
        chat._active_turn = None
        if chat._turn_lock.locked():
            chat._turn_lock.release()
    except Exception as e:
        logfire.error("turn failed: {error}", error=str(e))
        if chat._turn_span:
            chat._turn_span.set_attribute("error.type", type(e).__name__)
            chat._turn_span.__exit__(type(e), e, e.__traceback__)
            chat._turn_span = None
        chat._active_turn = None
        if chat._turn_lock.locked():
            chat._turn_lock.release()
        try:
            await ctx.send_error("turn-failed", str(e), chatId=chat_id)
        except Exception:
            pass


# =============================================================================
# WebSocket endpoint
# =============================================================================

@router.websocket("/ws")
async def websocket_chat(ws: WebSocket) -> None:
    """Command/event WebSocket endpoint.

    Receives commands, dispatches to handlers, broadcasts events.
    """
    await ws.accept()

    connections: set = ws.app.state.connections
    connections.add(ws)
    chats: dict[str, Chat] = ws.app.state.chats
    ctx = WsContext(ws, connections, chats)

    logfire.info("ws.connected", client=str(ws.client))

    # -- Immediate state push (no client command needed) -----------------------
    # Send app-state (chat list + global flags) then chat-loaded for
    # the suggested chat or the most recent one.
    try:
        from alpha_app.db import list_chats
        chat_list = await list_chats()

        await ctx.send_event(AppStateEvent(
            event="app-state",
            chats=chat_list,
            solitude=getattr(ws.app.state, "solitude", False),
            version=getattr(ws.app.state, "version", ""),
        ))

        # Determine which chat to load: query param hint or most recent.
        last_chat_param = ws.query_params.get("lastChat")
        chat_ids = {c["chatId"] for c in chat_list}
        target_id = None

        if last_chat_param and last_chat_param in chat_ids:
            target_id = last_chat_param
        elif chat_list:
            # Most recent by updatedAt
            target_id = max(chat_list, key=lambda c: c.get("updatedAt", 0))["chatId"]

        if target_id:
            # Reuse the join-chat handler logic
            await cmd_join_chat(ctx, JoinChatCommand(command="join-chat", chatId=target_id))

    except Exception as exc:
        logfire.error("ws.on-connect state push failed: {error}", error=str(exc))

    try:
        while True:
            raw = await ws.receive_json()

            # Parse and validate the command
            try:
                cmd = parse_command(raw)
            except ValidationError as e:
                # Bad command shape — tell the client
                cmd_id = raw.get("id") if isinstance(raw, dict) else None
                await ctx.send_error(
                    "invalid-command",
                    str(e),
                    id=cmd_id,
                )
                continue

            # Dispatch to handler
            handler = DISPATCH.get(cmd.command)
            if not handler:
                await ctx.send_error(
                    "unknown-command",
                    f"Unknown command: {cmd.command}",
                    id=cmd.id,
                )
                continue

            try:
                await handler(ctx, cmd)
            except Exception as exc:
                logfire.error(
                    "command handler failed: {command} {error}",
                    command=cmd.command,
                    error=str(exc),
                )
                await ctx.send_error(
                    "handler-error",
                    str(exc),
                    id=cmd.id,
                    chatId=getattr(cmd, "chatId", None),
                )

    except WebSocketDisconnect:
        logfire.debug("ws.disconnected", client=str(ws.client))
    except Exception as exc:
        logfire.warn("ws.error: {error}", error=str(exc))
    finally:
        connections.discard(ws)
        for task in ctx.streaming_tasks.values():
            if not task.done():
                task.cancel()
        for task in list(ctx.streaming_tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

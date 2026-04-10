"""Chat kernel — Chat subprocess lifecycle management.

The Chat class wraps a Claude subprocess with a state machine and reap timer.

State is a vector with two orthogonal dimensions:
  - Conversation: STARTING / READY / ENRICHING / RESPONDING / COLD
  - Suggest:      DISARMED / ARMED / FIRING

Events from Claude flow through _on_claude_event(), which dispatches to
focused handler methods via _EVENT_HANDLERS (see CHAT-V2.md).

See KERNEL.md for the full design.
"""

import asyncio
import contextvars
import json
import os
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

import logfire

from alpha_app import (
    AssistantEvent, Claude, ClaudeState, ErrorEvent, Event, ResultEvent,
    StreamEvent, SystemEvent, UserEvent,
)

from alpha_app.constants import CLAUDE_MODEL as MODEL, CONTEXT_WINDOW
from alpha_app.models import AssistantMessage, SystemMessage, UserMessage

# Mock mode — swap Claude for MockClaude in tests.
_MOCK_CLAUDE = os.environ.get("_ALPHA_MOCK_CLAUDE", "").strip() == "1"


def _make_claude(**kwargs) -> Claude:
    """Factory for Claude instances. Returns MockClaude when _ALPHA_MOCK_CLAUDE=1."""
    if _MOCK_CLAUDE:
        from alpha_app.mock_claude import MockClaude
        return MockClaude(**kwargs)
    return Claude(**kwargs)

# Idle chat timeout in seconds. After this, subprocess gets reaped.
# 60 minutes: long enough for Solitude's hourly breaths to keep the
# subprocess warm all night. During the day, most conversations don't
# have 60 minutes of dead silence. If they do, resurrect is seamless.
# Exposed as env var so e2e tests can shorten it.
REAP_TIMEOUT = int(os.environ.get("_ALPHA_REAP_TIMEOUT", "3600"))


def generate_chat_id() -> str:
    """Generate a URL-safe chat ID. 12 chars, ~72 bits of entropy."""
    return secrets.token_urlsafe(9)


def find_circadian_chat(
    chats: dict[str, "Chat"],
    *,
    dawn_hour: int = 6,
    now: float | None = None,
) -> "Chat | None":
    """Find the most recent non-solitude chat from the current circadian day.

    The circadian day runs from dawn_hour to dawn_hour (default 6 AM to 6 AM),
    NOT midnight to midnight. A chat created at 3 PM on March 31 still belongs
    to that circadian day at 1 AM on April 1.

    Returns the most recently updated matching chat, or None.
    """
    import pendulum

    if now is None:
        now = time.time()

    # Use local time — the circadian day is defined in wall-clock time
    now_dt = pendulum.from_timestamp(now, tz=pendulum.local_timezone())
    dawn_today = now_dt.replace(hour=dawn_hour, minute=0, second=0, microsecond=0)
    if now_dt < dawn_today:
        # Before 6 AM — circadian day started yesterday at 6 AM
        dawn_today = dawn_today.subtract(days=1)

    dawn_ts = dawn_today.timestamp()

    candidates = [
        c for c in chats.values()
        if c.id != "solitude"
        and c.created_at >= dawn_ts
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.updated_at)


# -- State vector dimensions --------------------------------------------------

# Wire value mapping: internal state names -> backward-compatible WebSocket values.
# When the frontend learns the new names, remove this mapping and use .value directly.
_CONVERSATION_WIRE = {
    "starting": "starting",
    "ready": "idle",
    "responding": "busy",
    "cold": "dead",
}


class ConversationState(Enum):
    """Conversation lifecycle. Claudes aren't alive or dead. They're warm or cold.

    CHAT-V2: ENRICHING removed (enrobe is external). Three meaningful states:
    COLD (no subprocess), READY (idle), RESPONDING (working).
    STARTING is transitional (during wake/resurrect).
    """
    STARTING = "starting"
    READY = "ready"
    RESPONDING = "responding"
    COLD = "cold"

    @property
    def wire_value(self) -> str:
        """Backward-compatible value for the WebSocket protocol."""
        return _CONVERSATION_WIRE.get(self.value, self.value)


# Backward compatibility — existing imports of ChatState still resolve.
ChatState = ConversationState


class Turn:
    """An exclusive turn on a Chat. Created by chat.turn() context manager.

    The holder can send one or more messages (steering). Nobody else can
    start a turn until this one ends. ResultEvent releases the lock.
    """

    def __init__(self, chat: "Chat") -> None:
        self._chat = chat

    async def send(self, msg_or_content) -> None:
        """Send a message within this turn. Can be called multiple times.

        Accepts either a UserMessage (preferred — handles persistence and
        broadcast) or raw content blocks (backward compat / steering).
        Auto-starts Claude if not alive.
        """
        chat = self._chat
        await chat._ensure_claude()

        # Set system instructions on the turn span now that Claude is alive.
        if chat._turn_span and chat._claude:
            chat._turn_span.set_attribute(
                "gen_ai.system_instructions",
                [{"type": "text", "content": getattr(chat._claude, "_assembled_system_prompt", "")}],
            )

        chat.updated_at = time.time()
        chat.state = ConversationState.RESPONDING

        if isinstance(msg_or_content, UserMessage):
            msg = msg_or_content
            # Extract title from first message if needed
            if not chat.title:
                for block in msg.content:
                    if block.get("type") == "text" and block.get("text"):
                        chat.title = block["text"][:80]
                        break
            chat.messages.append(msg)  # Born dirty
            await chat.flush()
            wire = msg.to_wire()
            await chat._broadcast({
                "event": "user-message",
                "chatId": chat.id,
                "messageId": wire.get("id", ""),
                "source": wire.get("source", "human"),
                "content": wire.get("content", []),
                "memories": wire.get("memories", []),
                "timestamp": wire.get("timestamp", ""),
            })
            chat.reset_output_tokens()
            await chat._claude.send(msg.to_content_blocks())
        else:
            # Raw content blocks — steering messages, backward compat
            await chat._claude.send(msg_or_content)

    async def response(self) -> "AssistantMessage | None":
        """Wait for Claude to finish. Returns the completed response."""
        await self._chat._claude._ready.wait()
        if self._chat.messages and isinstance(self._chat.messages[-1], AssistantMessage):
            return self._chat.messages[-1]
        return None


class Chat:
    """A single conversation. Owns a claude subprocess and its lifecycle.

    CHAT-V2 architecture: Turn class owns the send lifecycle.
    Chat provides turn() context manager and interject() primitive.
    Claude auto-starts via _ensure_claude() when needed.

    State: COLD (no subprocess) / STARTING / READY / RESPONDING.
    Suggest: DISARMED / ARMED / FIRING.
    """

    def __init__(self, *, id: str, claude: Claude | None = None) -> None:
        self.id = id
        self.session_uuid: str | None = None

        self.state = ConversationState.READY if claude else ConversationState.COLD

        self.title: str = ""
        self.created_at: float = time.time()
        self.updated_at: float = time.time()

        self._claude = claude

        # -- Smart Chat: the canonical conversation --
        self.messages: list[UserMessage | AssistantMessage | SystemMessage] = []
        self._current_assistant: AssistantMessage | None = None
        self._turn_span = None  # Logfire span — opened by turn handler, closed by callback
        self._output_parts: list[dict] = []  # Raw Messages API blocks for gen_ai.output.messages

        # Broadcast callback — set by whoever owns us (ws.py).
        # Signature: async (event_dict) -> None
        # Called for every WebSocket event to broadcast.
        self.on_broadcast: Callable[[dict], Awaitable[None]] | None = None

        # Turn lock — exclusive access for turns.
        self._turn_lock = asyncio.Lock()
        self._active_turn: "Turn | None" = None

        # Cached token state — survives subprocess death
        self._cached_token_count: int = 0
        self._cached_context_window: int = CONTEXT_WINDOW

        # Inertial turn counter — counts completed human-initiated turns.
        # Persisted in app.chats JSONB; survives reap/resurrect (same context
        # window continues via --resume). Used to gate post-turn suggest on
        # the N=3 cadence (fire on 1, 4, 7, 10, 13, ...).
        self._human_turn_count: int = 0

        # Fork source — if set, _ensure_claude will use --fork-session
        # instead of --resume. Set by clone(), consumed once.
        self._fork_from: str | None = None

        # Orientation flag — True means the next message needs orientation
        # injected. New chats need it on first message; resumed chats need it too.
        self._needs_orientation: bool = True

        # Intro memorables — set by the suggest pipeline after a turn,
        # consumed by enrobe on the next turn.
        # Approach light thresholds — each fires exactly once per session.
        # Reset on resurrect (context shrinks after compaction).
        self._crossed_yellow: bool = False
        self._crossed_red: bool = False

        # Topic injection tracking — which topics have been injected this
        # context window. Reset on compaction/resurrection (new window).
        self._injected_topics: set[str] = set()

        # Topic registry reference — set externally so wire_state can include
        # available topics. Not serialized — comes from app.state.topic_registry.
        self._topic_registry: Any | None = None

        # Broadcast callback — called after reap so all clients see the state change.
        # Set externally by the WS handler. Signature: async (chat_id: str) -> None.
        self.on_reap: Callable[[str], Awaitable[None]] | None = None

    @classmethod
    def from_db(cls, chat_id: str, created_at: float, updated_at: float, data: dict) -> "Chat":
        """Restore a Chat from Postgres row. Born COLD (no subprocess)."""
        chat = cls(id=chat_id)
        chat.session_uuid = data.get("session_uuid") or None
        chat.title = data.get("title", "")
        chat.state = ConversationState.COLD
        chat.created_at = created_at
        chat.updated_at = updated_at
        chat._cached_token_count = data.get("token_count", 0) or 0
        chat._cached_context_window = data.get("context_window", 0) or CONTEXT_WINDOW
        chat._injected_topics = set(data.get("injected_topics", []))
        chat._human_turn_count = data.get("human_turn_count", 0) or 0

        # Restore the recall seen-cache from persisted data.
        # Survives backend restarts — no more resurfacing stored memories.
        seen_ids = data.get("seen_ids", [])
        if seen_ids:
            from alpha_app.memories.recall import mark_seen
            mark_seen(chat_id, seen_ids)

        return chat

    def clone(self) -> "Chat":
        """Create a fork-ready copy of this Chat.

        Returns a new Chat with a fresh ID that, when its Claude subprocess
        starts, will fork from this chat's session (--fork-session). The
        original session is untouched. The clone is born COLD and headless —
        no broadcast, no WebSocket, no sidebar presence.

        Used by Dusk to create a ghost that writes a day capsule.
        """
        ghost = Chat(id=generate_chat_id())
        ghost.session_uuid = self.session_uuid  # Will fork from this
        ghost._fork_from = self.session_uuid     # Signal to _ensure_claude
        ghost._topic_registry = self._topic_registry
        ghost.title = f"[capsule] {self.title}"
        return ghost

    async def load_messages(self) -> None:
        """Load messages from app.messages into self.messages.

        Called after from_db() to hydrate the conversation. Messages are
        the source of truth for "gimme the fucking chat" on reconnect.
        """
        from alpha_app.db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT role, data FROM app.messages WHERE chat_id = $1 ORDER BY ordinal",
                self.id,
            )
        self.messages = []
        for row in rows:
            data = row["data"] if isinstance(row["data"], dict) else json.loads(row["data"])
            if row["role"] == "user":
                msg = UserMessage(
                    id=data.get("id", ""),
                    content=data.get("content", []),
                    source=data.get("source", "human"),
                    timestamp=data.get("timestamp"),
                )
                msg._dirty = False  # Loaded from Postgres — already persisted
                self.messages.append(msg)
            elif row["role"] == "assistant":
                msg = AssistantMessage(
                    id=data.get("id", ""),
                    parts=data.get("parts", []),
                    input_tokens=data.get("input_tokens", 0),
                    output_tokens=data.get("output_tokens", 0),
                    cache_creation_tokens=data.get("cache_creation_tokens", 0),
                    cache_read_tokens=data.get("cache_read_tokens", 0),
                    context_window=data.get("context_window", 0),
                    model=data.get("model"),
                    stop_reason=data.get("stop_reason"),
                    cost_usd=data.get("cost_usd", 0.0),
                    duration_ms=data.get("duration_ms", 0.0),
                    inference_count=data.get("inference_count", 0),
                )
                msg._dirty = False  # Loaded from Postgres — already persisted
                self.messages.append(msg)
            elif row["role"] == "system":
                msg = SystemMessage(
                    id=data.get("id", ""),
                    text=data.get("text", ""),
                    source=data.get("source", "system"),
                    timestamp=data.get("timestamp"),
                )
                msg._dirty = False  # Loaded from Postgres — already persisted
                self.messages.append(msg)

    def messages_to_wire(self) -> list[dict]:
        """Serialize messages for the 'gimme the fucking chat' payload."""
        result = []
        for msg in self.messages:
            if isinstance(msg, UserMessage):
                result.append({"role": "user", "data": msg.to_wire()})
            elif isinstance(msg, AssistantMessage):
                result.append({"role": "assistant", "data": msg.to_wire()})
            elif isinstance(msg, SystemMessage):
                result.append({"role": "system", "data": msg.to_wire()})
        return result

    # -- Persistence: Chat owns its own writes --------------------------------

    async def flush(self) -> int:
        """Write dirty messages to Postgres. Returns count written.

        UPSERT by (chat_id, ordinal). The ordinal is the array index in
        self.messages — the source of truth for ordering.

        Also persists chat metadata (token counts, title, etc.) so the
        sidebar and chat list stay current.

        This is the ONLY write path for messages. No external observer
        needed. Dawn, Solitude, WebSocket turns — all go through here.
        """
        dirty = [
            (i, msg) for i, msg in enumerate(self.messages)
            if getattr(msg, "_dirty", False)
        ]
        if not dirty:
            return 0

        from alpha_app.db import get_pool, persist_chat

        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    for ordinal, msg in dirty:
                        role = (
                            "user" if isinstance(msg, UserMessage)
                            else "assistant" if isinstance(msg, AssistantMessage)
                            else "system"
                        )
                        data = msg.to_db() if hasattr(msg, "to_db") else msg.to_wire()
                        await conn.execute(
                            """INSERT INTO app.messages (chat_id, ordinal, role, data)
                               VALUES ($1, $2, $3, $4)
                               ON CONFLICT (chat_id, ordinal)
                               DO UPDATE SET data = EXCLUDED.data, role = EXCLUDED.role""",
                            self.id, ordinal, role, data,
                        )
                        msg._dirty = False

            await persist_chat(self)
            logfire.info(
                "chat.flush: wrote {count} message(s) for chat={chat_id} ordinals={ordinals} total={total}",
                count=len(dirty),
                chat_id=self.id,
                ordinals=[i for i, _ in dirty],
                total=len(self.messages),
            )
        except Exception as e:
            logfire.error(
                "chat.flush failed: {error} chat={chat_id}",
                error=str(e),
                chat_id=self.id,
            )

        return len(dirty)

    # -- Token state properties -----------------------------------------------

    @property
    def token_count(self) -> int:
        if self._claude:
            live = self._claude.token_count
            # Prefer cached value when subprocess hasn't sent any messages yet
            # (e.g., during eager warmup — subprocess exists but token_count=0)
            return live if live > 0 else self._cached_token_count
        return self._cached_token_count

    @property
    def context_window(self) -> int:
        if self._claude:
            live = self._claude.context_window
            return live if live > 0 else self._cached_context_window
        return self._cached_context_window

    @property
    def input_tokens(self) -> int:
        return self._claude.input_tokens if self._claude else 0

    @property
    def total_input_tokens(self) -> int:
        """OTel-compliant total: uncached + cache_creation + cache_read."""
        return self._claude.total_input_tokens if self._claude else 0

    @property
    def cache_creation_tokens(self) -> int:
        return self._claude.cache_creation_tokens if self._claude else 0

    @property
    def cache_read_tokens(self) -> int:
        return self._claude.cache_read_tokens if self._claude else 0

    @property
    def output_tokens(self) -> int:
        return self._claude.output_tokens if self._claude else 0

    def reset_output_tokens(self) -> None:
        """Reset output token accumulator. Call at turn start."""
        if self._claude:
            self._claude.reset_output_tokens()

    @property
    def stop_reason(self) -> str | None:
        return self._claude.stop_reason if self._claude else None

    @property
    def response_model(self) -> str | None:
        return self._claude.response_model if self._claude else None

    @property
    def response_id(self) -> str | None:
        return self._claude.response_id if self._claude else None

    @property
    def usage_5h(self) -> float | None:
        return self._claude.usage_5h if self._claude else None

    @property
    def usage_7d(self) -> float | None:
        return self._claude.usage_7d if self._claude else None

    def pop_api_error(self) -> dict | None:
        """Return and clear the last API error, if any.
        Legacy — proxy was removed when we migrated to ClaudeSDKClient."""
        return None

    def to_data(self) -> dict:
        """Serialize chat metadata as a JSONB-ready dict."""
        from alpha_app.memories.recall import get_seen_ids
        seen = get_seen_ids(self.id)
        return {
            "session_uuid": self.session_uuid or "",
            "title": self.title,
            "created_at": self.created_at,
            "token_count": self.token_count,
            "context_window": self.context_window,
            "injected_topics": sorted(self._injected_topics) if self._injected_topics else [],
            "seen_ids": sorted(seen) if seen else [],
            "human_turn_count": self._human_turn_count,
        }

    def wire_state(self, **overrides) -> dict:
        """Build the chat-state data dict for WebSocket broadcasts.

        Includes all runtime state the frontend needs: state, title, session,
        tokens, context window, and injected topics. Extra fields can be
        passed as keyword arguments (e.g., state="starting").
        """
        data = {
            "state": self.state.wire_value,
            "title": self.title,
            "updatedAt": self.updated_at,
            "createdAt": self.created_at,
            "sessionUuid": self.session_uuid or "",
            "tokenCount": self.token_count,
            "contextWindow": self.context_window,
        }

        # Include topics only when registry is available.
        # Omitting the key entirely means the frontend preserves its existing
        # topics state (the `if (topics !== undefined)` guard in updateChatState).
        if self._topic_registry:
            topics: dict[str, str] = {}
            for name in self._topic_registry.list_topics():
                topics[name] = "on" if name in self._injected_topics else "off"
            data["topics"] = topics
        data.update(overrides)
        return data

    async def _ensure_claude(self) -> None:
        """Auto-start Claude if not alive. The CHAT-V2 auto-start primitive.

        Creates a fresh Claude subprocess with the current system prompt
        and MCP servers (both generated fresh — not cached). If the chat
        has a session_uuid, resumes it; otherwise starts fresh.

        Callers (Turn.send, interject) call this instead of checking
        is_alive themselves. wake() and resurrect() become internal.
        """
        if self._claude and self._claude.state != ClaudeState.STOPPED:
            return  # Already alive

        from alpha_app.tools import create_alpha_server

        # Create MCP servers fresh
        topic_registry = self._topic_registry
        mcp_servers = {"alpha": create_alpha_server(
            chat=self,
            topic_registry=topic_registry,
            session_id=self.id,
        )}

        from alpha_app.constants import DISALLOWED_TOOLS

        # Claude assembles its own system prompt at startup — no injection needed.
        self._claude = _make_claude(
            model=MODEL,
            permission_mode="bypassPermissions",
            mcp_servers=mcp_servers,
            disallowed_tools=DISALLOWED_TOOLS,
            on_event=self._on_claude_event,
        )
        self._claude._on_reap = self._on_claude_reap

        session_id = self.session_uuid  # None for fresh, UUID for resume
        fork = bool(self._fork_from)
        await self._claude.start(session_id, fork=fork)
        if fork:
            self._fork_from = None  # Consumed — don't fork again on resurrect

        self.state = ConversationState.READY
        if not session_id:
            # Fresh start — need orientation on first message
            self._needs_orientation = True
            self._injected_topics = set()

        logfire.info(
            "chat.lifecycle: auto-start chat={chat_id} session={session} mode={mode}",
            chat_id=self.id,
            session=session_id or "(new)",
            mode="fork" if fork else ("resume" if session_id else "fresh"),
        )

    async def wait_until_ready(self) -> "AssistantMessage | None":
        """Wait for Claude to finish, return the response.

        The universal "ask the duck, wait for the duck, read what the
        duck said" primitive. Claude owns the signal (_ready Event).
        Chat owns the interpretation (return the AssistantMessage).
        """
        if self._claude:
            await self._claude._ready.wait()
        if self.messages and isinstance(self.messages[-1], AssistantMessage):
            return self.messages[-1]
        return None

    def set_trace_context(self, ctx: dict | None) -> None:
        """Set trace context so proxy spans nest under the consumer's trace."""
        if self._claude:
            self._claude.set_trace_context(ctx)

    # -- Smart Chat: event dispatch --------------------------------------------
    #
    # Dispatch dict replaces the elif waterfall. Each handler has two concerns:
    #   1. Bookshelf — what does this event mean for messages[]?
    #   2. Broadcast — what should the browsers know?
    #
    # See CHAT-V2.md for the full design.

    _EVENT_HANDLERS: dict[type, str] = {
        StreamEvent: "_handle_stream",
        AssistantEvent: "_handle_assistant",
        UserEvent: "_handle_user_echo",
        ResultEvent: "_handle_result",
        SystemEvent: "_handle_system",
        ErrorEvent: "_handle_error",
    }

    async def _on_claude_event(self, event: Event) -> None:
        """Handle an event from Claude's continuous stdout drain.

        Dispatches to focused handler methods via _EVENT_HANDLERS.
        Called by the Claude._drain_stdout background task.
        """
        # -- Detect spontaneous responses (background task, system-initiated) --
        if (
            isinstance(event, (StreamEvent, AssistantEvent))
            and self.state == ConversationState.READY
        ):
            self.state = ConversationState.RESPONDING
            # Reap timer handled by Claude internally — no need to cancel here.

            if not self._turn_span:
                span = logfire.span(
                    "alpha.system-turn: spontaneous response",
                    **{
                        "gen_ai.operation.name": "chat",
                        "gen_ai.system": "anthropic",
                        "chat.id": self.id,
                        "chat.trigger": "system",
                    },
                )
                span.__enter__()
                self._turn_span = span
                self.set_trace_context(logfire.get_context())

            await self._broadcast({
                "event": "chat-state",
                "chatId": self.id,
                "state": self.state.wire_value,
            })

        # -- Dispatch to focused handler --
        handler_name = self._EVENT_HANDLERS.get(type(event))
        if handler_name:
            handler = getattr(self, handler_name)
            await handler(event)

    async def _broadcast(self, evt: dict) -> None:
        """Send a WebSocket event to all connected browsers."""
        if self.on_broadcast:
            await self.on_broadcast(evt)

    # -- Stream events: text/thinking deltas, tool-use starts ----------------

    async def _handle_stream(self, event: StreamEvent) -> None:
        """Bookshelf: accumulate deltas on AssistantMessage.
        Broadcast: send deltas live for streaming UX."""
        chat_id = self.id

        if event.delta_type == "text_delta":
            text = event.delta_text
            if text:
                await self._broadcast({
                    "event": "text-delta", "chatId": chat_id, "delta": text,
                })
                self._ensure_assistant()
                if self._current_assistant.parts and self._current_assistant.parts[-1]["type"] == "text":
                    self._current_assistant.parts[-1]["text"] += text
                else:
                    self._current_assistant.parts.append({"type": "text", "text": text})

        elif event.delta_type == "thinking_delta":
            text = event.delta_text
            if text:
                await self._broadcast({
                    "event": "thinking-delta", "chatId": chat_id, "delta": text,
                })
                self._ensure_assistant()
                if self._current_assistant.parts and self._current_assistant.parts[-1]["type"] == "thinking":
                    self._current_assistant.parts[-1]["thinking"] += text
                else:
                    self._current_assistant.parts.append({"type": "thinking", "thinking": text})

        elif event.delta_type == "input_json_delta":
            partial = event.delta_partial_json
            if partial:
                await self._broadcast({
                    "event": "tool-call-delta",
                    "chatId": chat_id,
                    "toolCallId": "",  # Not available on deltas, only on start
                    "delta": partial,
                })

        elif event.event_type == "content_block_start" and event.block_type == "tool_use":
            await self._broadcast({
                "event": "tool-call-start",
                "chatId": chat_id,
                "toolCallId": event.block_id,
                "name": event.block_name,
            })

    # -- Assistant events: complete content blocks ---------------------------

    async def _handle_assistant(self, event: AssistantEvent) -> None:
        """Bookshelf: accumulate tool_use blocks on AssistantMessage.
        Broadcast: send tool-call events for the frontend."""
        chat_id = self.id

        # Accumulate raw Messages API blocks for Logfire gen_ai.output.messages
        self._output_parts.extend(event.content)

        for block in event.content:
            if block.get("type") == "tool_use":
                tool_data = {
                    "toolCallId": block.get("id", ""),
                    "toolName": block.get("name", ""),
                    "args": block.get("input", {}),
                    "argsText": json.dumps(block.get("input", {})),
                }
                await self._broadcast({
                    "event": "tool-call-start",
                    "chatId": chat_id,
                    "toolCallId": tool_data["toolCallId"],
                    "name": tool_data["toolName"],
                })
                self._ensure_assistant()
                self._current_assistant.parts.append({"type": "tool-call", **tool_data})

    # -- User echo events: tool results, message confirmations ---------------

    async def _handle_user_echo(self, event: UserEvent) -> None:
        """Bookshelf: confirm pencil messages (pencil → ink), update tool results.
        Broadcast: confirmed user-message events, tool-result events."""
        chat_id = self.id

        # Pencil → ink: match the echo against unconfirmed UserMessages.
        # Walk in order. First match wins. Unmatched messages stay pencil
        # (orphaned — Claude never acknowledged them).
        echo_content = event.content
        for msg in self.messages:
            if isinstance(msg, UserMessage) and not msg._confirmed:
                if msg.to_content_blocks() == echo_content:
                    msg._confirmed = True
                    msg._dirty = True
                    # Re-broadcast as user-message with full metadata.
                    # Same event type as the initial send — progressive
                    # enhancement. Frontend reconciles by message ID.
                    wire = msg.to_wire()
                    await self._broadcast({
                        "event": "user-message",
                        "chatId": chat_id,
                        "messageId": wire.get("id", ""),
                        "source": wire.get("source", "human"),
                        "content": wire.get("content", []),
                        "memories": wire.get("memories", []),
                        "timestamp": wire.get("timestamp", ""),
                    })
                    break

        for block in event.content:
            if block.get("type") == "tool_result":
                tool_use_id = block.get("tool_use_id", "")
                content = block.get("content", "")
                if isinstance(content, list):
                    result_text = "\n".join(
                        b.get("text", "") for b in content if b.get("type") == "text"
                    )
                else:
                    result_text = str(content)

                await self._broadcast({
                    "event": "tool-call-result",
                    "chatId": chat_id,
                    "toolCallId": tool_use_id,
                    "name": "",  # Not available in tool_result echo
                    "args": {},
                    "result": result_text,
                })

                # Update the tool-call part in the accumulator
                if self._current_assistant:
                    for part in self._current_assistant.parts:
                        if part.get("type") == "tool-call" and part.get("toolCallId") == tool_use_id:
                            part["result"] = result_text
                            part["isError"] = block.get("is_error", False)
                            break

    # -- Result event: turn complete -----------------------------------------

    async def _handle_result(self, event: ResultEvent) -> None:
        """Bookshelf: finalize AssistantMessage with metadata, flush.
        Broadcast: assistant-message + chat-state + exceptions.
        Side effects: Logfire span closure, suggest pipeline."""
        chat_id = self.id

        if event.session_id:
            self.session_uuid = event.session_id

        # Finalize the assistant message
        if self._current_assistant and self._current_assistant.parts:
            msg = self._current_assistant
            msg.input_tokens = self.total_input_tokens
            msg.output_tokens = self.output_tokens
            msg.cache_creation_tokens = self.cache_creation_tokens
            msg.cache_read_tokens = self.cache_read_tokens
            msg.context_window = self.context_window
            msg.model = self.response_model
            msg.stop_reason = self.stop_reason
            msg.cost_usd = event.cost_usd
            msg.duration_ms = event.duration_ms
            msg.inference_count = event.num_turns

            # Message already in messages[] (appended by _ensure_assistant).
            # Mark dirty so metadata gets flushed.
            msg._dirty = True

            # Broadcast the coalesced assistant-message
            wire = msg.to_wire()
            await self._broadcast({
                "event": "assistant-message",
                "chatId": chat_id,
                "messageId": wire.get("id", ""),
                "content": wire.get("parts", []),
            })

            # Persist all dirty messages (including this one) to Postgres
            await self.flush()

        # Close the turn span with response attributes
        finalized_msg = self._current_assistant  # May be None if empty result
        self._current_assistant = None

        if self._turn_span:
            if finalized_msg:
                from alpha_app.routes.spans import set_turn_span_response
                set_turn_span_response(
                    self._turn_span, finalized_msg, self, self._output_parts
                )
            self._turn_span.__exit__(None, None, None)
            self._turn_span = None
        self._output_parts = []
        self.set_trace_context(None)

        # State transition
        self.state = ConversationState.READY

        # Release the turn lock — this turn is over. Jobs and reflection
        # can proceed.
        self._active_turn = None
        if self._turn_lock.locked():
            self._turn_lock.release()

        # Fire reflection — ONLY after human-initiated turns.
        # The guard is HERE, not in _post_turn_reflection. Reflection simply
        # doesn't get called unless the turn was human-initiated.
        last_user = next(
            (m for m in reversed(self.messages) if isinstance(m, UserMessage)),
            None,
        )
        if (
            finalized_msg
            and finalized_msg.text.strip()
            and last_user
            and last_user.source in ("human", "buzzer")
        ):
            # Inertial counter: increment once per completed human-initiated
            # turn. Counts at the ResultEvent boundary, so two user messages
            # in one turn (interjection) count as one turn — that's what we
            # want. Persisted in app.chats JSONB via to_data(); survives reap.
            self._human_turn_count += 1

            # N=3 cadence: fire reflection on turns 1, 4, 7, 10, 13, ...
            # (first term 1, common difference 3 — arithmetic sequence).
            should_reflect = (
                self._human_turn_count == 1
                or self._human_turn_count % 3 == 1
            )

            if should_reflect:
                user_text = " ".join(
                    b.get("text", "") for b in last_user.content if b.get("type") == "text"
                )
                if user_text.strip():
                    # Empty context so the reflection span is a SIBLING of
                    # alpha.turn, not a child. asyncio.create_task() inherits
                    # the parent's trace context; an empty Context() starts
                    # clean.
                    asyncio.create_task(
                        self._post_turn_reflection(user_text, finalized_msg.text),
                        context=contextvars.Context(),
                    )

        # Broadcast updated state
        await self._broadcast({
            "event": "chat-state",
            "chatId": chat_id,
            "state": self.state.wire_value,
        })

        # Context truncation detection
        _TRUNCATION_THRESHOLD = 50_000
        current_tokens = self.total_input_tokens
        if (
            self._cached_token_count > 0
            and current_tokens > 0
            and (self._cached_token_count - current_tokens) > _TRUNCATION_THRESHOLD
        ):
            from alpha_app.memories.recall import clear_seen
            clear_seen(chat_id)
            await self._broadcast({
                "event": "exception",
                "chatId": chat_id,
                "data": {
                    "exceptionType": "context-loss-detected",
                    "metadata": {
                        "previousTokens": self._cached_token_count,
                        "currentTokens": current_tokens,
                        "tokensLost": self._cached_token_count - current_tokens,
                    },
                },
            })

        # API error detection
        api_error = self.pop_api_error()
        if api_error:
            await self._broadcast({
                "event": "exception",
                "chatId": chat_id,
                "data": {
                    "exceptionType": "api-error",
                    "metadata": {
                        "status": api_error.get("status", 0),
                        "body": api_error.get("body", "")[:200],
                    },
                },
            })

    # -- System events: compact, task lifecycle ------------------------------

    async def _handle_system(self, event: SystemEvent) -> None:
        """Bookshelf: compact_boundary resets orientation/topics/seen cache.
        Broadcast: agent lifecycle events, system message cards."""
        chat_id = self.id

        if event.subtype == "compact_boundary":
            self._needs_orientation = True
            self._injected_topics = set()
            from alpha_app.memories.recall import clear_seen
            clear_seen(chat_id)
            logfire.info("compact_boundary detected", chat_id=chat_id)

        elif event.subtype == "task_started":
            await self._broadcast({
                "event": "agent-started",
                "chatId": chat_id,
                "data": {
                    "taskId": event.raw.get("task_id", ""),
                    "toolUseId": event.raw.get("tool_use_id", ""),
                    "description": event.raw.get("description", ""),
                    "prompt": (event.raw.get("prompt", "") or "")[:200],
                    "taskType": event.raw.get("task_type", ""),
                },
            })

        elif event.subtype == "task_progress":
            usage = event.raw.get("usage", {})
            await self._broadcast({
                "event": "agent-progress",
                "chatId": chat_id,
                "data": {
                    "taskId": event.raw.get("task_id", ""),
                    "toolUseId": event.raw.get("tool_use_id", ""),
                    "description": event.raw.get("description", ""),
                    "lastToolName": event.raw.get("last_tool_name", ""),
                    "toolUses": usage.get("tool_uses", 0),
                    "durationMs": usage.get("duration_ms", 0),
                },
            })

        elif event.subtype == "task_notification":
            import pendulum
            summary = event.raw.get("summary", "Background task completed")
            task_id = event.raw.get("task_id", "")
            status = event.raw.get("status", "completed")
            usage = event.raw.get("usage", {})

            await self._broadcast({
                "event": "agent-done",
                "chatId": chat_id,
                "data": {
                    "taskId": task_id,
                    "toolUseId": event.raw.get("tool_use_id", ""),
                    "status": status,
                    "summary": summary,
                    "totalTokens": usage.get("total_tokens", 0),
                    "toolUses": usage.get("tool_uses", 0),
                    "durationMs": usage.get("duration_ms", 0),
                },
            })

            # Create, persist, and broadcast the system message card
            sys_msg = SystemMessage(
                id=f"sys-{uuid.uuid4().hex[:12]}",
                text=summary,
                source="task_notification",
                timestamp=pendulum.now("America/Los_Angeles").format(
                    "ddd MMM D YYYY, h:mm A"
                ),
            )
            self.messages.append(sys_msg)
            await self.flush()

            await self._broadcast({
                "event": "system-message",
                "chatId": chat_id,
                "data": sys_msg.to_wire(),
            })

    # -- Error events --------------------------------------------------------

    async def _handle_error(self, event: ErrorEvent) -> None:
        """Bookshelf: nothing. Broadcast: error event."""
        await self._broadcast({
            "event": "error", "chatId": self.id, "code": "subprocess-error", "message": event.message,
        })

    def _ensure_assistant(self) -> None:
        """Lazily create the current assistant message accumulator.

        Appends to messages[] immediately so late-joiners (second browser
        tab, page reload) see the in-progress response via join-chat.
        Deltas accumulate on this same object in-place.
        """
        if self._current_assistant is None:
            self._current_assistant = AssistantMessage(
                id=f"msg-{uuid.uuid4().hex[:12]}"
            )
            self.messages.append(self._current_assistant)

    async def _post_turn_reflection(self, user_text: str, assistant_text: str) -> None:
        """Post-turn: send a system-reminder prompting Alpha to store anything
        worth remembering from the previous exchange.

        Alpha responds via cortex.store tool calls. The reminder is explicit
        that the conversation is still waiting on Jeffery's actual reply, so
        Alpha should not proceed with any pending conversational thread.
        Only called after human-initiated turns (guard in _handle_result).

        Own Logfire span (sibling to the turn span) with gen_ai attributes.
        """
        from alpha_app.reflection import build_reflection_reminder
        from alpha_app.db import fetch_unclaimed_flags, claim_flags

        # Highlighter: surface any silent flags dropped during the exchange.
        flags = await fetch_unclaimed_flags(self.id)
        flag_notes = [f["note"] for f in flags]
        reminder_text = build_reflection_reminder(flag_notes)

        with logfire.span("alpha.reflection", **{
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "anthropic",
            "chat.id": self.id,
            "reflection.user_text_len": len(user_text),
            "reflection.assistant_text_len": len(assistant_text),
            "reflection.flag_count": len(flag_notes),
        }) as span:
            try:
                span.set_attribute("gen_ai.input.messages", [
                    {"role": "user", "content": user_text[:200]},
                    {"role": "assistant", "content": assistant_text[:200]},
                ])

                # Send as own turn with source="reflection" so the source
                # guard in _handle_result sees it and does NOT fire this
                # again. The UserMessage constructor auto-stamps timestamp
                # via default_factory — no explicit timestamp kwarg needed.
                from alpha_app.models import UserMessage as UM
                msg = UM(
                    id=f"reflection-{uuid.uuid4().hex[:8]}",
                    content=[{"type": "text", "text": reminder_text}],
                    source="reflection",
                )
                async with await self.turn() as t:
                    # Set trace context so stdout drain and proxy attach to this span
                    self.set_trace_context(logfire.get_context())
                    await t.send(msg)
                    response = await t.response()
                    self.set_trace_context(None)

                if response:
                    span.set_attribute("reflection.had_text_output", bool(response.text))
                    span.set_attribute("gen_ai.output.messages", [
                        {"role": "assistant", "content": response.text[:200] if response.text else ""}
                    ])
                    span.set_attribute("gen_ai.usage.output_tokens", self.output_tokens)

                # Claim flags after the reminder has been delivered. We claim
                # even if response was empty — the flags were surfaced, and
                # re-surfacing them on the next reminder would be noise.
                if flags:
                    await claim_flags([f["id"] for f in flags])

            except Exception as e:
                span.set_attribute("error.type", type(e).__name__)
                logfire.debug("post-turn reflection failed: {error}", error=str(e))

    # -- Approach light -------------------------------------------------------

    def check_approach_threshold(self) -> str | None:
        """Check if a new approach light threshold was just crossed.

        Returns 'yellow' or 'red' if a NEW threshold was crossed, None otherwise.
        Each threshold fires exactly once per session. Resets on resurrect.

        DISABLED: Thresholds were calibrated for 200K (65%/75% with compact
        at ~80%). At 1M we don't know where auto-compact triggers. Disabled
        until we can recalibrate. Remove this early return when ready.
        """
        return None  # Disabled — recalibrate for 1M context window

        if self.context_window <= 0:
            return None
        ratio = self.token_count / self.context_window
        if ratio >= 0.75 and not self._crossed_red:
            self._crossed_red = True
            self._crossed_yellow = True  # Skip yellow if we jumped past it
            return "red"
        if ratio >= 0.65 and not self._crossed_yellow:
            self._crossed_yellow = True
            return "yellow"
        return None

    # -- Turn lock: exclusive access primitives --------------------------------

    async def turn(self):
        """Acquire an exclusive turn. Context manager.

        Usage:
            async with chat.turn() as t:
                await t.send(msg)
                response = await t.response()

        The turn lock guarantees nobody else can send during your turn.
        ResultEvent ends the turn (releases the lock). Multiple send()
        calls within one turn are allowed (steering messages).
        """
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _turn_cm():
            await self._ensure_claude()  # auto-start if needed
            await self._claude._ready.wait()  # wait until Claude is free
            await self._turn_lock.acquire()
            t = Turn(self)
            self._active_turn = t
            try:
                yield t
            finally:
                self._active_turn = None
                if self._turn_lock.locked():
                    self._turn_lock.release()

        return _turn_cm()

    async def interject(self, content: list[dict]) -> None:
        """Fire-and-forget message. Bypasses turn lock.

        No response tracking. Used for alarms, nudges, AutoRAG.
        The message enters Claude's stdin queue and gets processed
        between API calls. Auto-starts Claude if not alive.
        """
        await self._ensure_claude()
        await self._claude.send(content)

    # -- Legacy send/begin_turn removed -----------------------------------------
    # All callers now use Turn.send() or Chat.interject().
    # See CHAT-V2.md for the new architecture.

    async def interrupt(self) -> None:
        """Interrupt the current turn. * -> COLD."""
        await self.reap()

    async def _on_claude_reap(self) -> None:
        """Called by Claude's reap timer when it stops itself.

        Claude has already stopped. Chat just needs to update its own state
        and broadcast the change.
        """
        self._snapshot_token_state()
        logfire.info(
            "chat.lifecycle: claude self-reaped chat={chat_id} session={session}",
            chat_id=self.id,
            session=self.session_uuid or "?",
        )
        self._claude = None
        self.state = ConversationState.COLD
        if self.on_reap:
            await self.on_reap(self.id)

    async def reap(self) -> None:
        """Kill the subprocess. * -> COLD."""
        if self._claude:
            self._snapshot_token_state()
            logfire.info(
                "chat.lifecycle: reap chat={chat_id} session={session} tokens={tokens}",
                chat_id=self.id,
                session=self.session_uuid or "?",
                tokens=self._cached_token_count,
            )
            try:
                await self._claude.stop()
            except Exception:
                logfire.warn(
                    "chat.lifecycle: reap stop failed chat={chat_id}",
                    chat_id=self.id,
                )
            self._claude = None
        self.state = ConversationState.COLD

    # wake() and resurrect() removed — CHAT-V2.
    # _ensure_claude() handles all lifecycle: auto-starts on send, resumes if
    # session_uuid exists, starts fresh otherwise.
    # Only exception: dawn.py nightnight, which uses _make_claude directly
    # because it needs a custom MCP server (letter_to_tomorrow tool).

    # -- Token state snapshot --------------------------------------------------

    def _snapshot_token_state(self) -> None:
        if self._claude:
            count = self._claude.token_count
            if count > 0:
                self._cached_token_count = count
            window = self._claude.context_window
            if window > 0:
                self._cached_context_window = window

    # -- Reap timer (delegated to Claude) --------------------------------------
    # Claude self-manages its idle timer. These are convenience wrappers
    # for the transition period. Chat calls them; they delegate to Claude.

    def _start_reap_timer(self) -> None:
        if self._claude:
            self._claude._start_reap_timer()

    def _cancel_reap_timer(self) -> None:
        if self._claude:
            self._claude._cancel_reap_timer()



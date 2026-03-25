"""Chat kernel — Chat subprocess lifecycle management.

The Chat class wraps a Claude subprocess with a state machine and reap timer.

State is a vector with two orthogonal dimensions:
  - Conversation: STARTING / READY / ENRICHING / RESPONDING / COLD
  - Suggest:      DISARMED / ARMED / FIRING

Each dimension evolves independently. Off-diagonal couplings:
  - begin_turn() disarms suggest (new turn resets the cycle)
  - ResultEvent arms suggest (turn complete, ready for analysis)
  - conversation -> COLD -> suggest -> DISARMED (cleanup)

See KERNEL.md for the full design.
"""

import asyncio
import json
import os
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any, AsyncIterator

import logfire

from alpha_app import (
    AssistantEvent, Claude, ErrorEvent, Event, ResultEvent,
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


# -- State vector dimensions --------------------------------------------------

# Wire value mapping: internal state names -> backward-compatible WebSocket values.
# When the frontend learns the new names, remove this mapping and use .value directly.
_CONVERSATION_WIRE = {
    "starting": "starting",
    "ready": "idle",
    "enriching": "busy",
    "responding": "busy",
    "cold": "dead",
}


class ConversationState(Enum):
    """First dimension of the state vector: the conversation lifecycle.

    Claudes aren't alive or dead. They're warm or cold.
    """
    STARTING = "starting"
    READY = "ready"
    ENRICHING = "enriching"
    RESPONDING = "responding"
    COLD = "cold"

    @property
    def wire_value(self) -> str:
        """Backward-compatible value for the WebSocket protocol."""
        return _CONVERSATION_WIRE.get(self.value, self.value)


class SuggestState(Enum):
    """Second dimension of the state vector: the metacognitive pipeline."""
    DISARMED = "disarmed"
    ARMED = "armed"
    FIRING = "firing"


# Backward compatibility — existing imports of ChatState still resolve.
ChatState = ConversationState


class Chat:
    """A single conversation. Owns a claude subprocess and its lifecycle.

    State vector: (conversation, suggest)
      conversation: STARTING / READY / ENRICHING / RESPONDING / COLD
      suggest:      DISARMED / ARMED / FIRING

    Lifecycle:
        wake()          -> COLD -> STARTING -> READY (fresh start)
        begin_turn()    -> READY -> ENRICHING, suggest -> DISARMED
        send()          -> ENRICHING/READY -> RESPONDING (or interjection)
        events()        -> RESPONDING -> READY, suggest -> ARMED
        interrupt()     -> * -> COLD, suggest -> DISARMED
        reap()          -> * -> COLD, suggest -> DISARMED
        resurrect()     -> COLD -> STARTING -> READY (resume existing session)
    """

    def __init__(self, *, id: str, claude: Claude | None = None) -> None:
        self.id = id
        self.session_uuid: str | None = None

        # State vector: two orthogonal dimensions
        self.state = ConversationState.READY if claude else ConversationState.COLD
        self.suggest = SuggestState.DISARMED

        self.title: str = ""
        self.created_at: float = time.time()
        self.updated_at: float = time.time()

        self._claude = claude
        self._system_prompt: str = ""  # Stored for resurrection
        self._reap_task: asyncio.Task | None = None

        # -- Smart Chat: the canonical conversation --
        self.messages: list[UserMessage | AssistantMessage | SystemMessage] = []
        self._current_assistant: AssistantMessage | None = None
        self._turn_span = None  # Logfire span — opened by turn handler, closed by callback
        self._output_parts: list[dict] = []  # Raw Messages API blocks for gen_ai.output.messages

        # Broadcast callback — set by whoever owns us (ws.py).
        # Signature: async (event_dict) -> None
        # Called for every WebSocket event to broadcast.
        self.on_broadcast: Callable[[dict], Awaitable[None]] | None = None

        # Cached token state — survives subprocess death
        self._cached_token_count: int = 0
        self._cached_context_window: int = CONTEXT_WINDOW

        # Orientation flag — True means the next message needs orientation
        # injected. New chats need it on first message; resumed chats need it too.
        self._needs_orientation: bool = True

        # Intro memorables — set by the suggest pipeline after a turn,
        # consumed by enrobe on the next turn.
        self._pending_intro: str | None = None

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
    def from_db(cls, chat_id: str, updated_at: float, data: dict) -> "Chat":
        """Restore a Chat from Postgres row. Born COLD (no subprocess)."""
        chat = cls(id=chat_id)
        chat.session_uuid = data.get("session_uuid") or None
        chat.title = data.get("title", "")
        chat.state = ConversationState.COLD
        chat.created_at = data.get("created_at", 0) or 0
        chat.updated_at = updated_at
        chat._cached_token_count = data.get("token_count", 0) or 0
        chat._cached_context_window = data.get("context_window", 0) or CONTEXT_WINDOW
        chat._injected_topics = set(data.get("injected_topics", []))

        # Restore the recall seen-cache from persisted data.
        # Survives backend restarts — no more resurfacing stored memories.
        seen_ids = data.get("seen_ids", [])
        if seen_ids:
            from alpha_app.memories.recall import mark_seen
            mark_seen(chat_id, seen_ids)

        return chat

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
                self.messages.append(UserMessage(
                    id=data.get("id", ""),
                    content=data.get("content", []),
                    source=data.get("source", "human"),
                    timestamp=data.get("timestamp"),
                ))
            elif row["role"] == "assistant":
                self.messages.append(AssistantMessage(
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
                ))
            elif row["role"] == "system":
                self.messages.append(SystemMessage(
                    id=data.get("id", ""),
                    text=data.get("text", ""),
                    source=data.get("source", "system"),
                    timestamp=data.get("timestamp"),
                ))

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
        """Return and clear the last API error from the proxy, if any."""
        if self._claude and self._claude._proxy:
            return self._claude._proxy.pop_api_error()
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

    def set_trace_context(self, ctx: dict | None) -> None:
        """Set trace context so proxy spans nest under the consumer's trace."""
        if self._claude:
            self._claude.set_trace_context(ctx)

    # -- Smart Chat: event callback -------------------------------------------

    async def _on_claude_event(self, event: Event) -> None:
        """Handle an event from Claude's continuous stdout drain.

        This replaces stream_chat_events(). Every event from Claude flows
        through here. The method classifies the event, broadcasts the
        appropriate WebSocket message, and accumulates the AssistantMessage.

        Called by the Claude._drain_stdout background task.
        """
        chat_id = self.id

        async def _broadcast(evt: dict) -> None:
            if self.on_broadcast:
                await self.on_broadcast(evt)

        # -- Detect spontaneous responses (background task, system-initiated) --
        # If Claude starts producing content while we're READY (not RESPONDING),
        # it means Claude initiated a response without a user message.
        # Transition to RESPONDING and broadcast so the frontend goes modal.
        if (
            isinstance(event, (StreamEvent, AssistantEvent))
            and self.state == ConversationState.READY
        ):
            self.state = ConversationState.RESPONDING
            self._cancel_reap_timer()

            # Open a lightweight span for observability
            if not self._turn_span:
                span = logfire.span(
                    "alpha.system-turn: spontaneous response",
                    **{
                        "gen_ai.operation.name": "chat",
                        "gen_ai.system": "anthropic",
                        "chat.id": chat_id,
                        "chat.trigger": "system",
                    },
                )
                span.__enter__()
                self._turn_span = span
                self.set_trace_context(logfire.get_context())

            await _broadcast({
                "type": "chat-state",
                "chatId": chat_id,
                "data": self.wire_state(),
            })

        if isinstance(event, StreamEvent):
            # -- Streaming deltas: broadcast live, accumulate into message --
            if event.delta_type == "text_delta":
                text = event.delta_text
                if text:
                    await _broadcast({
                        "type": "text-delta", "chatId": chat_id, "data": text,
                    })
                    self._ensure_assistant()
                    if self._current_assistant.parts and self._current_assistant.parts[-1]["type"] == "text":
                        self._current_assistant.parts[-1]["text"] += text
                    else:
                        self._current_assistant.parts.append({"type": "text", "text": text})

            elif event.delta_type == "thinking_delta":
                text = event.delta_text
                if text:
                    await _broadcast({
                        "type": "thinking-delta", "chatId": chat_id, "data": text,
                    })
                    self._ensure_assistant()
                    if self._current_assistant.parts and self._current_assistant.parts[-1]["type"] == "thinking":
                        self._current_assistant.parts[-1]["thinking"] += text
                    else:
                        self._current_assistant.parts.append({"type": "thinking", "thinking": text})

            elif event.delta_type == "input_json_delta":
                partial = event.delta_partial_json
                if partial:
                    await _broadcast({
                        "type": "tool-use-delta",
                        "chatId": chat_id,
                        "data": {"index": event.index, "partialJson": partial},
                    })

            elif event.event_type == "content_block_start" and event.block_type == "tool_use":
                await _broadcast({
                    "type": "tool-use-start",
                    "chatId": chat_id,
                    "data": {
                        "toolCallId": event.block_id,
                        "toolName": event.block_name,
                        "index": event.index,
                    },
                })

        elif isinstance(event, AssistantEvent):
            # -- Complete content blocks from Claude --
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
                    await _broadcast({
                        "type": "tool-call",
                        "chatId": chat_id,
                        "data": tool_data,
                    })
                    self._ensure_assistant()
                    self._current_assistant.parts.append({"type": "tool-call", **tool_data})

        elif isinstance(event, UserEvent):
            # -- User echoes: tool results and message echoes --
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

                    await _broadcast({
                        "type": "tool-result",
                        "chatId": chat_id,
                        "data": {
                            "toolCallId": tool_use_id,
                            "result": result_text,
                            "isError": block.get("is_error", False),
                        },
                    })

                    # Update the tool-call part in the accumulator
                    if self._current_assistant:
                        for part in self._current_assistant.parts:
                            if part.get("type") == "tool-call" and part.get("toolCallId") == tool_use_id:
                                part["result"] = result_text
                                part["isError"] = block.get("is_error", False)
                                break

        elif isinstance(event, ResultEvent):
            # -- Turn complete: finalize, persist, reset --
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

                self.messages.append(msg)

                # Broadcast the coalesced assistant-message
                await _broadcast({
                    "type": "assistant-message",
                    "chatId": chat_id,
                    "data": msg.to_wire(),
                })

                # Persist to app.messages
                try:
                    from alpha_app.db import store_message, next_message_ordinal, persist_chat
                    ordinal = await next_message_ordinal(chat_id)
                    await store_message(chat_id, ordinal, "assistant", msg.to_db())
                    await persist_chat(self)
                except Exception as e:
                    logfire.error("persist failed: {error}", error=str(e))

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
            # Clear trace context so stdout traces between turns don't
            # attach to the now-closed span.
            self.set_trace_context(None)

            # State transitions
            self.state = ConversationState.READY
            self.suggest = SuggestState.ARMED
            self._start_reap_timer()

            # Fire suggest in the dead time after the turn completes
            if finalized_msg and finalized_msg.text.strip():
                # Extract user text from the last UserMessage
                user_text = ""
                for m in reversed(self.messages):
                    if isinstance(m, UserMessage):
                        user_text = " ".join(
                            b.get("text", "") for b in m.content if b.get("type") == "text"
                        )
                        break
                if user_text.strip():
                    asyncio.create_task(self._run_suggest(user_text, finalized_msg.text))

            # Broadcast updated state
            await _broadcast({
                "type": "chat-state",
                "chatId": chat_id,
                "data": self.wire_state(),
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
                await _broadcast({
                    "type": "exception",
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
                await _broadcast({
                    "type": "exception",
                    "chatId": chat_id,
                    "data": {
                        "exceptionType": "api-error",
                        "metadata": {
                            "status": api_error.get("status", 0),
                            "body": api_error.get("body", "")[:200],
                        },
                    },
                })

        elif isinstance(event, SystemEvent):
            if event.subtype == "compact_boundary":
                self._needs_orientation = True
                self._injected_topics = set()
                from alpha_app.memories.recall import clear_seen
                clear_seen(chat_id)
                logfire.info("compact_boundary detected", chat_id=chat_id)

            elif event.subtype == "task_notification":
                import pendulum
                summary = event.raw.get("summary", "Background task completed")
                task_id = event.raw.get("task_id", "")
                status = event.raw.get("status", "completed")

                # Create, persist, and broadcast the system message
                sys_msg = SystemMessage(
                    id=f"sys-{uuid.uuid4().hex[:12]}",
                    text=summary,
                    source="task_notification",
                    timestamp=pendulum.now("America/Los_Angeles").format(
                        "ddd MMM D YYYY, h:mm A"
                    ),
                )
                self.messages.append(sys_msg)

                # Persist to app.messages
                try:
                    from alpha_app.db import store_message, next_message_ordinal
                    ordinal = await next_message_ordinal(chat_id)
                    await store_message(chat_id, ordinal, "system", sys_msg.to_db())
                except Exception as e:
                    logfire.error("persist system message failed: {error}", error=str(e))

                await _broadcast({
                    "type": "system-message",
                    "chatId": chat_id,
                    "data": sys_msg.to_wire(),
                })

        elif isinstance(event, ErrorEvent):
            await _broadcast({
                "type": "error", "chatId": chat_id, "data": event.message,
            })

    def _ensure_assistant(self) -> None:
        """Lazily create the current assistant message accumulator."""
        if self._current_assistant is None:
            self._current_assistant = AssistantMessage(
                id=f"msg-{uuid.uuid4().hex[:12]}"
            )

    async def _run_suggest(self, user_text: str, assistant_text: str) -> None:
        """Fire-and-forget suggest pipeline. Populates self._pending_intro."""
        from alpha_app.suggest import suggest, format_intro_block
        self.suggest = SuggestState.FIRING
        try:
            memorables = await suggest(user_text, assistant_text)
            block = format_intro_block(memorables)
            if block:
                self._pending_intro = block
                logfire.info(
                    "suggest: {count} memorables",
                    count=len(memorables),
                    memorables=memorables,
                    chat_id=self.id,
                )
        except Exception:
            pass
        finally:
            self.suggest = SuggestState.DISARMED

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

    # -- Turn lifecycle -------------------------------------------------------

    def begin_turn(self, content: list[dict] | None = None) -> None:
        """Begin a new turn. READY -> ENRICHING, suggest -> DISARMED.

        Cancels the reap timer, extracts title from original (pre-enrobe)
        content, and transitions to ENRICHING. Called before enrobe().
        """
        if self.state in (ConversationState.COLD, ConversationState.STARTING):
            raise RuntimeError(
                f"Chat {self.id} cannot begin turn in state {self.state.value}"
            )

        self._cancel_reap_timer()
        self.state = ConversationState.ENRICHING
        self.suggest = SuggestState.DISARMED
        self.updated_at = time.time()

        # Title from original content (before enrobe adds enrichment blocks)
        if content and not self.title:
            for block in content:
                if block.get("type") == "text" and block.get("text"):
                    self.title = block["text"][:80]
                    break

    async def send(self, content: list[dict]) -> None:
        """Send a message to claude. ENRICHING/READY -> RESPONDING, or interjection.

        Full duplex: claude reads stdin between tool calls. Sending while
        RESPONDING feeds the message to the subprocess, which absorbs it
        into the current turn. No state change — just write to stdin.
        """
        if self.state == ConversationState.COLD:
            raise RuntimeError(f"Chat {self.id} is COLD — resurrect first")
        if self.state == ConversationState.STARTING:
            raise RuntimeError(f"Chat {self.id} is still STARTING")

        if self.state == ConversationState.RESPONDING:
            # Interjection — feed to subprocess, no state change.
            await self._claude.send(content)
            return

        # New turn: ENRICHING -> RESPONDING (after enrobe)
        # or READY -> RESPONDING (direct send, backward compat)
        if self.state == ConversationState.READY:
            # Direct send without begin_turn() — backward compat path.
            self._cancel_reap_timer()
            self.updated_at = time.time()
            if not self.title:
                for block in content:
                    if block.get("type") == "text" and block.get("text"):
                        self.title = block["text"][:80]
                        break

        self.state = ConversationState.RESPONDING
        await self._claude.send(content)

    async def events(self) -> AsyncIterator[Event]:
        """Stream events until the turn completes.

        On ResultEvent: conversation -> READY, suggest -> ARMED.
        """
        if not self._claude:
            raise RuntimeError(f"Chat {self.id} has no subprocess")

        try:
            async for event in self._claude.events():
                if isinstance(event, ResultEvent):
                    if event.session_id:
                        self.session_uuid = event.session_id
                    # State vector transition: turn complete
                    self.state = ConversationState.READY
                    self.suggest = SuggestState.ARMED
                    self._start_reap_timer()
                    yield event
                    return
                yield event
        except Exception:
            self._snapshot_token_state()
            self.state = ConversationState.COLD
            self.suggest = SuggestState.DISARMED
            self._claude = None
            raise

    async def interrupt(self) -> None:
        """Interrupt the current turn. * -> COLD."""
        await self.reap()

    async def reap(self) -> None:
        """Kill the subprocess. * -> COLD, suggest -> DISARMED."""
        self._cancel_reap_timer()
        if self._claude:
            self._snapshot_token_state()
            try:
                await self._claude.stop()
            except Exception:
                pass
            self._claude = None
        self.state = ConversationState.COLD
        self.suggest = SuggestState.DISARMED

    async def wake(
        self,
        system_prompt: str = "",
        mcp_servers: dict[str, Any] | None = None,
        disallowed_tools: list[str] | None = None,
    ) -> None:
        """Start a fresh Claude subprocess. COLD -> STARTING -> READY."""
        if self.state != ConversationState.COLD:
            raise RuntimeError(f"Can only wake COLD chats, not {self.state.value}")

        prompt = system_prompt or self._system_prompt
        self.state = ConversationState.STARTING

        try:
            self._claude = _make_claude(
                model=MODEL,
                system_prompt=prompt or None,
                permission_mode="bypassPermissions",
                mcp_servers=mcp_servers,
                disallowed_tools=disallowed_tools,
                on_event=self._on_claude_event if self.on_broadcast else None,
            )
            await self._claude.start(None)  # Fresh start, no resume

            self.state = ConversationState.READY
            self._needs_orientation = True
            self._injected_topics = set()
            self._start_reap_timer()
        except Exception:
            self.state = ConversationState.COLD
            self._claude = None
            raise

    async def resurrect(
        self,
        system_prompt: str = "",
        session_uuid: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        disallowed_tools: list[str] | None = None,
    ) -> None:
        """Bring a COLD chat back to life via --resume. COLD -> STARTING -> READY."""
        if self.state != ConversationState.COLD:
            raise RuntimeError(f"Can only resurrect COLD chats, not {self.state.value}")

        _uuid = session_uuid or self.session_uuid
        if not _uuid:
            raise RuntimeError(f"Chat {self.id}: cannot resurrect without a session UUID")

        prompt = system_prompt or self._system_prompt

        self.state = ConversationState.STARTING

        try:
            self._claude = _make_claude(
                model=MODEL,
                system_prompt=prompt or None,
                permission_mode="bypassPermissions",
                mcp_servers=mcp_servers,
                disallowed_tools=disallowed_tools,
                on_event=self._on_claude_event if self.on_broadcast else None,
            )
            await self._claude.start(_uuid)

            self.state = ConversationState.READY
            self._crossed_yellow = False
            self._crossed_red = False
            self._needs_orientation = False
            # Don't reset _injected_topics — they persist across resurrections.
            # Only reset on wake() (new chat) and compact_boundary (new window).
            self._start_reap_timer()

        except Exception:
            self.state = ConversationState.COLD
            self._claude = None
            raise

    # -- Token state snapshot --------------------------------------------------

    def _snapshot_token_state(self) -> None:
        if self._claude:
            count = self._claude.token_count
            if count > 0:
                self._cached_token_count = count
            window = self._claude.context_window
            if window > 0:
                self._cached_context_window = window

    # -- Reap timer -----------------------------------------------------------

    def _start_reap_timer(self) -> None:
        self._cancel_reap_timer()
        self._reap_task = asyncio.create_task(self._reap_after(REAP_TIMEOUT))

    def _cancel_reap_timer(self) -> None:
        if self._reap_task:
            # Don't cancel yourself. When _reap_after() calls reap() which
            # calls us, we ARE the reap task. Self-cancellation raises
            # CancelledError at the next await inside reap(), which prevents
            # reap() from ever setting state = COLD. The birthday bug.
            if self._reap_task is not asyncio.current_task():
                self._reap_task.cancel()
            self._reap_task = None

    async def _reap_after(self, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
            await self.reap()
            # Broadcast the state change so all clients update their sidebar dots.
            if self.on_reap:
                await self.on_reap(self.id)
        except asyncio.CancelledError:
            pass



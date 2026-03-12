"""Chat kernel — Chat + Holster for subprocess lifecycle management.

The Chat class wraps a Claude subprocess with a state machine and reap timer.
The Holster keeps one warm subprocess ready for instant startup.

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
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any, AsyncIterator

from alpha_app import Claude, Event, ResultEvent

# The model IS part of the definition. When we upgrade, we change this line.
MODEL = "claude-opus-4-6"

# Mock mode — swap Claude for MockClaude in tests.
_MOCK_CLAUDE = os.environ.get("_ALPHA_MOCK_CLAUDE", "").strip() == "1"


def _make_claude(**kwargs) -> "Claude":
    """Factory for Claude instances. Returns MockClaude when _ALPHA_MOCK_CLAUDE=1."""
    if _MOCK_CLAUDE:
        from alpha_app.mock_claude import MockClaude
        return MockClaude(**kwargs)
    return Claude(**kwargs)

# Idle chat timeout in seconds. After this, subprocess gets reaped.
# Exposed as env var so e2e tests can shorten it. Defaults to 10 minutes.
REAP_TIMEOUT = int(os.environ.get("_ALPHA_REAP_TIMEOUT", "600"))


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
        from_holster()  -> (READY, DISARMED)
        begin_turn()    -> READY -> ENRICHING, suggest -> DISARMED
        send()          -> ENRICHING/READY -> RESPONDING (or interjection)
        events()        -> RESPONDING -> READY, suggest -> ARMED
        interrupt()     -> * -> COLD, suggest -> DISARMED
        reap()          -> * -> COLD, suggest -> DISARMED
        resurrect()     -> COLD -> STARTING -> READY
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

        # Cached token state — survives subprocess death
        self._cached_token_count: int = 0
        self._cached_context_window: int = 200_000

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

        # Broadcast callback — called after reap so all clients see the state change.
        # Set externally by the WS handler. Signature: async (chat_id: str) -> None.
        self.on_reap: Callable[[str], Awaitable[None]] | None = None

    @classmethod
    def from_holster(cls, *, id: str, claude: Claude, system_prompt: str = "") -> "Chat":
        """Create a Chat with a pre-warmed Claude. Born READY."""
        chat = cls(id=id, claude=claude)
        chat._system_prompt = system_prompt
        chat._start_reap_timer()
        return chat

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
        chat._cached_context_window = data.get("context_window", 0) or 200_000
        return chat

    # -- Token state properties -----------------------------------------------

    @property
    def token_count(self) -> int:
        if self._claude:
            return self._claude.token_count
        return self._cached_token_count

    @property
    def context_window(self) -> int:
        if self._claude:
            return self._claude.context_window
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

    def to_data(self) -> dict:
        """Serialize chat metadata as a JSONB-ready dict."""
        return {
            "session_uuid": self.session_uuid or "",
            "title": self.title,
            "created_at": self.created_at,
            "token_count": self.token_count,
            "context_window": self.context_window,
        }

    def set_trace_context(self, ctx: dict | None) -> None:
        """Set trace context so proxy spans nest under the consumer's trace."""
        if self._claude:
            self._claude.set_trace_context(ctx)

    # -- Approach light -------------------------------------------------------

    def check_approach_threshold(self) -> str | None:
        """Check if a new approach light threshold was just crossed.

        Returns 'yellow' or 'red' if a NEW threshold was crossed, None otherwise.
        Each threshold fires exactly once per session. Resets on resurrect.

        Thresholds:
          65% → yellow (start wrapping up)
          75% → red (compaction imminent, ~80-85%)
        """
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

    async def resurrect(
        self,
        system_prompt: str = "",
        session_uuid: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
    ) -> None:
        """Bring a COLD chat back to life via --resume. COLD -> STARTING -> READY."""
        if self.state != ConversationState.COLD:
            raise RuntimeError(f"Can only resurrect COLD chats, not {self.state.value}")

        uuid = session_uuid or self.session_uuid
        if not uuid:
            raise RuntimeError(f"Chat {self.id}: cannot resurrect without a session UUID")

        prompt = system_prompt or self._system_prompt

        self.state = ConversationState.STARTING

        try:
            self._claude = _make_claude(
                model=MODEL,
                system_prompt=prompt or None,
                permission_mode="bypassPermissions",
                mcp_servers=mcp_servers,
            )
            await self._claude.start(uuid)

            self.state = ConversationState.READY
            self._crossed_yellow = False
            self._crossed_red = False
            self._needs_orientation = True
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


class Holster:
    """One in the chamber. Always keeps one warm claude subprocess ready."""

    def __init__(
        self,
        system_prompt: str = "",
        mcp_servers: dict[str, Any] | None = None,
    ) -> None:
        self._system_prompt = system_prompt
        self._mcp_servers = mcp_servers or {}
        self._warm: Claude | None = None
        self._warming: asyncio.Task | None = None

    @property
    def ready(self) -> bool:
        return self._warm is not None

    async def warm(self) -> None:
        """Start warming a new Claude in the background."""
        if self._warming or self._warm:
            return
        self._warming = asyncio.create_task(self._do_warm())

    async def _do_warm(self) -> None:
        try:
            claude = _make_claude(
                model=MODEL,
                system_prompt=self._system_prompt or None,
                permission_mode="bypassPermissions",
                mcp_servers=self._mcp_servers or None,
            )
            await claude.start(None)
            self._warm = claude
            self._warming = None
        except Exception:
            self._warming = None

    async def claim(self) -> Claude:
        """Take the warm Claude. Immediately start warming a replacement."""
        if self._warm is not None:
            claude = self._warm
            self._warm = None
            self._warming = None
            await self.warm()
            return claude

        if self._warming is not None:
            await self._warming
            return await self.claim()

        await self._do_warm()
        return await self.claim()

    async def shutdown(self) -> None:
        if self._warming:
            self._warming.cancel()
            try:
                await self._warming
            except asyncio.CancelledError:
                pass
            self._warming = None
        if self._warm:
            try:
                await self._warm.stop()
            except Exception:
                pass
            self._warm = None

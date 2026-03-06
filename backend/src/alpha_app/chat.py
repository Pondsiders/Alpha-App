"""Chat kernel — Chat + Holster for subprocess lifecycle management.

The Chat class wraps a Claude subprocess with a state machine and reap timer.
The Holster keeps one warm subprocess ready for instant startup.

See KERNEL.md for the full design.
"""

import asyncio
import secrets
import time
from enum import Enum
from typing import AsyncIterator

from alpha_sdk import Claude, Event, ResultEvent

# The mannequin model. Haiku for speed and cheapness.
MODEL = "claude-haiku-4-5-20251001"

# Idle chat timeout in seconds. After this, subprocess gets reaped.
REAP_TIMEOUT = 600  # 10 minutes


def generate_chat_id() -> str:
    """Generate a URL-safe chat ID. 12 chars, ~72 bits of entropy."""
    return secrets.token_urlsafe(9)


class ChatState(Enum):
    STARTING = "starting"
    IDLE = "idle"
    BUSY = "busy"
    DEAD = "dead"


class Chat:
    """A single conversation. Owns a claude subprocess and its lifecycle.

    State machine:
        from_holster() -> IDLE   (born warm)
        send()         -> IDLE->BUSY
        events()       -> (yields until ResultEvent) -> BUSY->IDLE
        interrupt()    -> *->DEAD
        reap()         -> *->DEAD
        resurrect()    -> DEAD->STARTING->IDLE
    """

    def __init__(self, *, id: str, claude: Claude | None = None) -> None:
        self.id = id
        self.session_uuid: str | None = None
        self.state = ChatState.IDLE if claude else ChatState.DEAD
        self.title: str = ""
        self.created_at: float = time.time()
        self.updated_at: float = time.time()

        self._claude = claude
        self._system_prompt: str = ""  # Stored for resurrection
        self._reap_task: asyncio.Task | None = None

        # Cached token state — survives subprocess death
        self._cached_token_count: int = 0
        self._cached_context_window: int = 200_000

    @classmethod
    def from_holster(cls, *, id: str, claude: Claude, system_prompt: str = "") -> "Chat":
        """Create a Chat with a pre-warmed Claude. Born IDLE."""
        chat = cls(id=id, claude=claude)
        chat._system_prompt = system_prompt
        chat._start_reap_timer()
        return chat

    @classmethod
    def from_db(cls, chat_id: str, updated_at: float, data: dict) -> "Chat":
        """Restore a Chat from Postgres row. Born DEAD (no subprocess)."""
        chat = cls(id=chat_id)
        chat.session_uuid = data.get("session_uuid") or None
        chat.title = data.get("title", "")
        chat.state = ChatState.DEAD
        chat.created_at = data.get("created_at", 0) or 0
        chat.updated_at = updated_at
        chat._cached_token_count = data.get("token_count", 0) or 0
        chat._cached_context_window = data.get("context_window", 0) or 200_000
        return chat

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

    # -- Per-turn usage (live only, from proxy SSE sniffing) ------------------

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

    async def send(self, content: list[dict]) -> None:
        """Send a message to claude. IDLE->BUSY."""
        if self.state == ChatState.DEAD:
            raise RuntimeError(f"Chat {self.id} is DEAD — resurrect first")
        if self.state == ChatState.STARTING:
            raise RuntimeError(f"Chat {self.id} is still STARTING")
        if self.state == ChatState.BUSY:
            raise RuntimeError(f"Chat {self.id} is already BUSY")

        self._cancel_reap_timer()
        self.state = ChatState.BUSY
        self.updated_at = time.time()

        if not self.title:
            for block in content:
                if block.get("type") == "text" and block.get("text"):
                    self.title = block["text"][:80]
                    break

        await self._claude.send(content)

    async def events(self) -> AsyncIterator[Event]:
        """Stream events until the turn completes."""
        if not self._claude:
            raise RuntimeError(f"Chat {self.id} has no subprocess")

        try:
            async for event in self._claude.events():
                if isinstance(event, ResultEvent):
                    if event.session_id:
                        self.session_uuid = event.session_id
                    self.state = ChatState.IDLE
                    self._start_reap_timer()
                    yield event
                    return
                yield event
        except Exception:
            self._snapshot_token_state()
            self.state = ChatState.DEAD
            self._claude = None
            raise

    async def interrupt(self) -> None:
        """Interrupt the current turn. *->DEAD."""
        await self.reap()

    async def reap(self) -> None:
        """Kill the subprocess. *->DEAD."""
        self._cancel_reap_timer()
        if self._claude:
            self._snapshot_token_state()
            try:
                await self._claude.stop()
            except Exception:
                pass
            self._claude = None
        self.state = ChatState.DEAD

    async def resurrect(self, system_prompt: str = "", session_uuid: str | None = None) -> None:
        """Bring a DEAD chat back to life via --resume. DEAD->STARTING->IDLE."""
        if self.state != ChatState.DEAD:
            raise RuntimeError(f"Can only resurrect DEAD chats, not {self.state.value}")

        uuid = session_uuid or self.session_uuid
        if not uuid:
            raise RuntimeError(f"Chat {self.id}: cannot resurrect without a session UUID")

        prompt = system_prompt or self._system_prompt

        self.state = ChatState.STARTING

        try:
            self._claude = Claude(
                model=MODEL,
                system_prompt=prompt or None,
                permission_mode="bypassPermissions",
            )
            await self._claude.start(uuid)

            # --resume loads the session JSONL and restores context silently.
            # No events are emitted — the subprocess goes READY and waits
            # for input.  (A previous drain loop here caused a deadlock:
            # we waited for events that would never come.)

            self.state = ChatState.IDLE
            self._start_reap_timer()

        except Exception:
            self.state = ChatState.DEAD
            self._claude = None
            raise

    # -- Token state snapshot ----------------------------------------------

    def _snapshot_token_state(self) -> None:
        if self._claude:
            count = self._claude.token_count
            if count > 0:
                self._cached_token_count = count
            window = self._claude.context_window
            if window > 0:
                self._cached_context_window = window

    # -- Reap timer -------------------------------------------------------

    def _start_reap_timer(self) -> None:
        self._cancel_reap_timer()
        self._reap_task = asyncio.create_task(self._reap_after(REAP_TIMEOUT))

    def _cancel_reap_timer(self) -> None:
        if self._reap_task:
            self._reap_task.cancel()
            self._reap_task = None

    async def _reap_after(self, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
            await self.reap()
        except asyncio.CancelledError:
            pass


class Holster:
    """One in the chamber. Always keeps one warm claude subprocess ready."""

    def __init__(self, system_prompt: str = "") -> None:
        self._system_prompt = system_prompt
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
            claude = Claude(
                model=MODEL,
                system_prompt=self._system_prompt or None,
                permission_mode="bypassPermissions",
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

"""Chat kernel — Chat + Holster for subprocess lifecycle management.

The Chat class wraps a Claude subprocess with a state machine and reap timer.
The Holster keeps one warm subprocess ready for instant startup.

See KERNEL.md for the full design.
"""

import asyncio
import logging
import secrets
import time
from enum import Enum
from typing import AsyncIterator

from alpha_sdk import Claude, Event, ResultEvent

log = logging.getLogger(__name__)

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
        log.info("Chat %s: born IDLE (from holster)", id)
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

    def to_data(self) -> dict:
        """Serialize chat metadata as a JSONB-ready dict."""
        return {
            "session_uuid": self.session_uuid or "",
            "title": self.title,
            "created_at": self.created_at,
            "token_count": self.token_count,
            "context_window": self.context_window,
        }

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

        log.info("Chat %s: IDLE->BUSY", self.id)
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
                    log.info(
                        "Chat %s: BUSY->IDLE (session=%s, cost=$%.4f)",
                        self.id,
                        self.session_uuid[:8] if self.session_uuid else "?",
                        event.cost_usd,
                    )
                    yield event
                    return
                yield event
        except Exception:
            log.exception("Chat %s: subprocess error, going DEAD", self.id)
            self._snapshot_token_state()
            self.state = ChatState.DEAD
            self._claude = None
            raise

    async def interrupt(self) -> None:
        """Interrupt the current turn. *->DEAD."""
        log.info("Chat %s: interrupted -> DEAD", self.id)
        await self.reap()

    async def reap(self) -> None:
        """Kill the subprocess. *->DEAD."""
        self._cancel_reap_timer()
        if self._claude:
            self._snapshot_token_state()
            try:
                await self._claude.stop()
            except Exception as e:
                log.warning("Chat %s: error stopping claude: %s", self.id, e)
            self._claude = None
        self.state = ChatState.DEAD
        log.info("Chat %s: DEAD", self.id)

    async def resurrect(self, system_prompt: str = "", session_uuid: str | None = None) -> None:
        """Bring a DEAD chat back to life via --resume. DEAD->STARTING->IDLE."""
        if self.state != ChatState.DEAD:
            raise RuntimeError(f"Can only resurrect DEAD chats, not {self.state.value}")

        uuid = session_uuid or self.session_uuid
        if not uuid:
            raise RuntimeError(f"Chat {self.id}: cannot resurrect without a session UUID")

        prompt = system_prompt or self._system_prompt

        self.state = ChatState.STARTING
        log.info("Chat %s: DEAD->STARTING (resuming %s...)", self.id, uuid[:8])

        try:
            self._claude = Claude(
                model=MODEL,
                system_prompt=prompt or None,
                permission_mode="bypassPermissions",
            )
            await self._claude.start(uuid)

            # Drain resume metadata events
            async for event in self._claude.events():
                if isinstance(event, ResultEvent):
                    if event.session_id:
                        self.session_uuid = event.session_id
                    break

            self.state = ChatState.IDLE
            self._start_reap_timer()
            log.info("Chat %s: STARTING->IDLE", self.id)

        except Exception:
            log.exception("Chat %s: resurrection failed -> DEAD", self.id)
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
            log.info("Chat %s: idle timeout (%ds) — reaping", self.id, int(seconds))
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
            log.info("Holster: warm claude ready")
        except Exception:
            log.exception("Holster: failed to warm claude")
            self._warming = None

    async def claim(self) -> Claude:
        """Take the warm Claude. Immediately start warming a replacement."""
        if self._warm is not None:
            claude = self._warm
            self._warm = None
            self._warming = None
            await self.warm()
            log.info("Holster: claude claimed, warming replacement")
            return claude

        if self._warming is not None:
            log.info("Holster: waiting for warm-up to complete...")
            await self._warming
            return await self.claim()

        log.info("Holster: cold start (nothing warm)")
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
            except Exception as e:
                log.warning("Holster: error stopping warm claude: %s", e)
            self._warm = None
        log.info("Holster: shutdown complete")

"""Chat kernel — Chat + Holster for subprocess lifecycle management.

Phase 1: Single-chat refactor. Same WebSocket protocol. Different internals.
The Chat class wraps AlphaClient with a state machine and reap timer.
The Holster keeps one warm subprocess ready for instant startup.

See KERNEL.md for the full design.
"""

import asyncio
import logging
import secrets
import time
from enum import Enum
from typing import AsyncIterator

from alpha_sdk import AlphaClient, Event, ResultEvent

log = logging.getLogger(__name__)

# The mannequin model. Haiku for speed and cheapness.
# Note: claude-haiku-4-20250514 does NOT exist. Don't repeat this mistake.
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
        interrupt()    -> *->DEAD (AlphaClient has no cancel-turn API)
        reap()         -> *->DEAD
        resurrect()    -> DEAD->STARTING->IDLE
    """

    def __init__(self, *, id: str, client: AlphaClient | None = None) -> None:
        self.id = id
        self.session_uuid: str | None = None
        self.state = ChatState.IDLE if client else ChatState.DEAD
        self.title: str = ""
        self.created_at: float = time.time()
        self.updated_at: float = time.time()

        self._client = client
        self._reap_task: asyncio.Task | None = None

        # Cached token state — survives subprocess death and Redis round-trips
        self._cached_token_count: int = 0
        self._cached_context_window: int = 200_000

    @classmethod
    def from_holster(cls, *, id: str, client: AlphaClient) -> "Chat":
        """Create a Chat with a pre-warmed client. Born IDLE."""
        chat = cls(id=id, client=client)
        chat._start_reap_timer()
        log.info("Chat %s: born IDLE (from holster)", id)
        return chat

    @classmethod
    def from_redis(cls, chat_id: str, data: dict[str, str]) -> "Chat":
        """Restore a Chat from Redis metadata. Born DEAD (no subprocess)."""
        chat = cls(id=chat_id)
        chat.session_uuid = data.get("session_uuid") or None
        chat.title = data.get("title", "")
        chat.state = ChatState.DEAD
        chat.created_at = float(data.get("created_at", 0) or 0)
        chat.updated_at = float(data.get("updated_at", 0) or 0)
        chat._cached_token_count = int(data.get("token_count", 0) or 0)
        chat._cached_context_window = int(data.get("context_window", 0) or 0) or 200_000
        return chat

    @property
    def token_count(self) -> int:
        """Current input token count. Falls back to cached value if no live subprocess."""
        if self._client:
            return self._client.token_count
        return self._cached_token_count

    @property
    def context_window(self) -> int:
        """Context window size. Falls back to cached value if no live subprocess."""
        if self._client:
            return self._client.context_window
        return self._cached_context_window

    def serialize(self) -> dict[str, str]:
        """Serialize chat metadata for Redis persistence.

        Note: state is NOT persisted — it's a runtime property of the subprocess.
        After a restart, all chats are DEAD regardless of what they were before.
        """
        return {
            "session_uuid": self.session_uuid or "",
            "title": self.title,
            "created_at": str(self.created_at),
            "updated_at": str(self.updated_at),
            "token_count": str(self.token_count),
            "context_window": str(self.context_window),
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

        # Set title from first user message
        if not self.title:
            for block in content:
                if block.get("type") == "text" and block.get("text"):
                    self.title = block["text"][:80]
                    break

        log.info("Chat %s: IDLE->BUSY", self.id)
        await self._client.send(content)

    async def events(self) -> AsyncIterator[Event]:
        """Stream events from the subprocess until the turn completes.

        Yields all events including the final ResultEvent.
        On ResultEvent: captures session UUID, BUSY->IDLE, reap timer restarts.
        On subprocess crash: BUSY->DEAD, exception propagates.
        """
        if not self._client:
            raise RuntimeError(f"Chat {self.id} has no subprocess")

        try:
            async for event in self._client.events():
                if isinstance(event, ResultEvent):
                    # Capture session UUID (None until first ResultEvent)
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
            # Subprocess crashed — go DEAD
            log.exception("Chat %s: subprocess error, going DEAD", self.id)
            self._snapshot_token_state()
            self.state = ChatState.DEAD
            self._client = None
            raise

    async def interrupt(self) -> None:
        """Interrupt the current turn.

        AlphaClient has no cancel-turn API, so interrupt = kill subprocess.
        *->DEAD.
        """
        log.info("Chat %s: interrupted -> DEAD", self.id)
        await self.reap()

    async def reap(self) -> None:
        """Kill the subprocess. *->DEAD. Safe to call on already-DEAD chats."""
        self._cancel_reap_timer()
        if self._client:
            self._snapshot_token_state()
            try:
                await self._client.stop()
            except Exception as e:
                log.warning("Chat %s: error stopping client: %s", self.id, e)
            self._client = None
        self.state = ChatState.DEAD
        log.info("Chat %s: DEAD", self.id)

    async def resurrect(self, session_uuid: str | None = None) -> None:
        """Bring a DEAD chat back to life via --resume. DEAD->STARTING->IDLE."""
        if self.state != ChatState.DEAD:
            raise RuntimeError(f"Can only resurrect DEAD chats, not {self.state.value}")

        uuid = session_uuid or self.session_uuid
        if not uuid:
            raise RuntimeError(f"Chat {self.id}: cannot resurrect without a session UUID")

        self.state = ChatState.STARTING
        log.info("Chat %s: DEAD->STARTING (resuming %s...)", self.id, uuid[:8])

        try:
            self._client = AlphaClient(
                model=MODEL,
                system_prompt="",
                permission_mode="bypassPermissions",
            )
            await self._client.start(uuid)

            # Drain resume metadata events (claude emits these before it's ready)
            async for event in self._client.events():
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
            self._client = None
            raise

    # -- Token state snapshot ----------------------------------------------

    def _snapshot_token_state(self) -> None:
        """Snapshot token state from live client before it goes away."""
        if self._client:
            count = self._client.token_count
            if count > 0:
                self._cached_token_count = count
            window = self._client.context_window
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
    """One in the chamber. Always keeps one warm claude subprocess ready.

    On app startup, warm() is called. When a new chat claims the warm
    subprocess via claim(), a replacement starts warming immediately.

    The holster subprocess is NOT on the reap timer — it stays warm
    indefinitely until claimed or the app shuts down.
    """

    def __init__(self) -> None:
        self._warm: AlphaClient | None = None
        self._warming: asyncio.Task | None = None

    @property
    def ready(self) -> bool:
        """True if a warm client is available for immediate claim."""
        return self._warm is not None

    async def warm(self) -> None:
        """Start warming a new client in the background."""
        if self._warming or self._warm:
            return  # Already warming or warm
        self._warming = asyncio.create_task(self._do_warm())

    async def _do_warm(self) -> None:
        """Spawn a claude subprocess (new session) and wait until ready."""
        try:
            client = AlphaClient(
                model=MODEL,
                system_prompt="",
                permission_mode="bypassPermissions",
            )
            await client.start(None)  # New session, no --resume
            self._warm = client
            self._warming = None
            log.info("Holster: warm client ready")
        except Exception:
            log.exception("Holster: failed to warm client")
            self._warming = None

    async def claim(self) -> AlphaClient:
        """Take the warm client. Immediately start warming a replacement."""
        if self._warm is not None:
            client = self._warm
            self._warm = None
            self._warming = None  # Clear stale ref
            await self.warm()  # Chamber the next round
            log.info("Holster: client claimed, warming replacement")
            return client

        # Rare: replacement still warming
        if self._warming is not None:
            log.info("Holster: waiting for warm-up to complete...")
            await self._warming
            return await self.claim()

        # Nothing at all — cold start
        log.info("Holster: cold start (nothing warm)")
        await self._do_warm()
        return await self.claim()

    async def shutdown(self) -> None:
        """Clean up on app shutdown."""
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
                log.warning("Holster: error stopping warm client: %s", e)
            self._warm = None
        log.info("Holster: shutdown complete")

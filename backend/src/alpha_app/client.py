"""Alpha client — wraps AlphaClient for the mannequin.

Session handling:
- At startup: no client, no session
- First request: create client with AlphaClient, start()
- Track current session_id
- If next request has different sessionId: stop(), recreate, start()
- If same sessionId: reuse existing client, just send()

AlphaClient from alpha_sdk handles the claude subprocess directly.
No system prompt, no memory, no observers — bare mirepoix.
This is the mannequin: Haiku, empty soul, just the dress.
"""

import logging
from typing import AsyncIterator

from alpha_sdk import AlphaClient, Event, ResultEvent

log = logging.getLogger(__name__)

# The mannequin model. Haiku for speed and cheapness.
# Note: claude-haiku-4-20250514 does NOT exist. Don't repeat this mistake.
MODEL = "claude-haiku-4-5-20251001"


class MannekinClient:
    """Wrapper around AlphaClient for the mannequin test.

    Lazy initialization: no client at startup.
    Creates client on first request, recreates on session change.
    """

    def __init__(self) -> None:
        self._client: AlphaClient | None = None
        self._current_session_id: str | None = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    @property
    def current_session_id(self) -> str | None:
        return self._current_session_id

    async def ensure_session(self, session_id: str | None) -> None:
        """Ensure we have a client connected to the right session.

        If no client exists, create one.
        If client exists but for different session, stop and recreate.
        If client exists for same session, do nothing.
        """
        if self._client is None:
            await self._create_client(session_id)
        elif session_id != self._current_session_id:
            log.info("Session change: %s -> %s", self._current_session_id, session_id)
            await self._stop_client()
            await self._create_client(session_id)
        # else: same session, reuse existing client

    async def _create_client(self, session_id: str | None) -> None:
        """Create a new AlphaClient, optionally resuming a session."""
        self._client = AlphaClient(
            model=MODEL,
            system_prompt="",  # Empty = no default prompt. The mannequin has no soul.
            permission_mode="bypassPermissions",
        )
        await self._client.start(session_id)
        self._current_session_id = session_id

        desc = f"resuming {session_id[:8]}..." if session_id else "new session"
        log.info("Client connected (%s, model=%s)", desc, MODEL)

        # When resuming a session, the subprocess emits metadata events
        # (including a ResultEvent with cost=$0) before it's ready for new
        # messages. Drain those so events() in the chat route only sees
        # events from the actual user message.
        if session_id:
            log.info("Draining resume events...")
            async for event in self._client.events():
                if isinstance(event, ResultEvent):
                    self._current_session_id = event.session_id or session_id
                    log.info("Resume drain complete (session=%s)", self._current_session_id[:8])
                    break

    async def _stop_client(self) -> None:
        """Stop the current client."""
        if self._client:
            try:
                await self._client.stop()
            except Exception as e:
                log.warning("Error stopping client: %s", e)
            self._client = None
            log.info("Client disconnected")

    def update_session_id(self, session_id: str) -> None:
        """Update the current session ID after receiving it from Claude.

        Called when we start a new session (session_id=None) and Claude
        gives us back the actual session ID in ResultEvent.
        """
        if self._current_session_id is None and session_id:
            log.info("New session ID: %s...", session_id[:8])
            self._current_session_id = session_id

    async def send(self, content: list[dict]) -> None:
        """Send a message to Claude."""
        if not self._client:
            raise RuntimeError("Client not connected - call ensure_session first")
        await self._client.send(content)

    async def events(self) -> AsyncIterator[Event]:
        """Stream events from Claude."""
        if not self._client:
            raise RuntimeError("Client not connected")
        async for event in self._client.events():
            yield event

    async def shutdown(self) -> None:
        """Clean shutdown."""
        await self._stop_client()


# Global singleton
client = MannekinClient()

"""claude.py — The Claude class. Lifecycle wrapper around ClaudeSDKClient.

Delegates subprocess management, protocol, streaming, MCP dispatch,
and session resume/fork to the published Claude Agent SDK. Adds:
- Idle reap timer (self-managed)
- System prompt assembly (fresh on every start)
- Event mapping (SDK messages → our Event types)
- Trace context for Logfire nesting

Usage:
    claude = Claude()  # system prompt assembled automatically at startup
    claude._on_event = my_handler     # events flow through callback
    await claude.start()              # New session
    await claude.start("abc-123")     # Resume session
    await claude.send([{"type": "text", "text": "Hello!"}])
    await claude._ready.wait()        # Wait for turn to complete
    await claude.stop()
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

import logfire

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage as SDKAssistantMessage,
    ResultMessage as SDKResultMessage,
    SystemMessage as SDKSystemMessage,
    UserMessage as SDKUserMessage,
)
from claude_agent_sdk.types import StreamEvent as SDKStreamEvent, TextBlock


# -- State machine ------------------------------------------------------------


class ClaudeState(Enum):
    """Lifecycle states for the claude subprocess."""

    IDLE = auto()      # Not started
    STARTING = auto()  # Subprocess spawned, init handshake in progress
    RUNNING = auto()   # Init complete, drain running, accepting messages
    READY = RUNNING    # Backward compat alias
    STOPPED = auto()   # Shut down (graceful or error)


# -- Events (our domain types — Chat uses these) ------------------------------


@dataclass
class Event:
    """Base event from the claude process."""
    raw: dict
    is_replay: bool = False


@dataclass
class InitEvent(Event):
    """Capabilities advertisement from the init handshake."""
    model: str = ""
    tools: list = field(default_factory=list)
    mcp_servers: list = field(default_factory=list)


@dataclass
class UserEvent(Event):
    """User message echo — content blocks."""
    content: list = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(
            block.get("text", "")
            for block in self.content
            if block.get("type") == "text"
        )


@dataclass
class AssistantEvent(Event):
    """Assistant response — content blocks."""
    content: list = field(default_factory=list)
    error: str | None = None  # SDK AssistantMessageError (e.g. "unknown", "rate_limit")

    @property
    def text(self) -> str:
        return "".join(
            block.get("text", "")
            for block in self.content
            if block.get("type") == "text"
        )


@dataclass
class ResultEvent(Event):
    """End-of-turn result with session info and cost."""
    session_id: str = ""
    cost_usd: float = 0.0
    num_turns: int = 0
    duration_ms: int = 0
    is_error: bool = False


@dataclass
class SystemEvent(Event):
    """System message from claude (init, compact_boundary, task_*)."""
    subtype: str = ""


@dataclass
class ErrorEvent(Event):
    """Error from claude (process death, parse failure, etc.)."""
    message: str = ""


@dataclass
class StreamEvent(Event):
    """Streaming delta — Messages API format."""
    inner: dict = field(default_factory=dict)

    @property
    def event_type(self) -> str:
        return self.inner.get("type", "")

    @property
    def index(self) -> int:
        return self.inner.get("index", 0)

    @property
    def delta_type(self) -> str:
        return self.inner.get("delta", {}).get("type", "")

    @property
    def delta_text(self) -> str:
        delta = self.inner.get("delta", {})
        return delta.get("text", "") or delta.get("thinking", "")

    @property
    def delta_partial_json(self) -> str:
        return self.inner.get("delta", {}).get("partial_json", "")

    @property
    def block_type(self) -> str:
        return self.inner.get("content_block", {}).get("type", "")

    @property
    def block_id(self) -> str:
        return self.inner.get("content_block", {}).get("id", "")

    @property
    def block_name(self) -> str:
        return self.inner.get("content_block", {}).get("name", "")


# -- Replay -------------------------------------------------------------------


def _find_session_path(session_id: str, sessions_dir: Path | None = None) -> Path:
    """Locate a session's JSONL transcript file."""
    if sessions_dir:
        path = sessions_dir / f"{session_id}.jsonl"
    else:
        cwd = os.path.realpath(os.getcwd()).replace("/", "-")
        path = Path.home() / ".claude" / "projects" / cwd / f"{session_id}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"Session transcript not found: {path}")
    return path


async def replay_session(
    session_id: str,
    sessions_dir: Path | None = None,
) -> AsyncIterator[Event]:
    """Read a session's JSONL transcript and yield replay events."""
    path = _find_session_path(session_id, sessions_dir)

    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        record_type = record.get("type")
        message = record.get("message", {})
        content = message.get("content", [])

        if record_type == "user":
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            yield UserEvent(raw=record, content=content, is_replay=True)
        elif record_type == "assistant":
            if isinstance(content, list):
                yield AssistantEvent(raw=record, content=content, is_replay=True)


# -- Claude -------------------------------------------------------------------


class Claude:
    """Lifecycle wrapper around ClaudeSDKClient.

    Handles: start/stop, idle reaping, system prompt assembly,
    event mapping (SDK messages → our Event types).

    Delegates: subprocess management, protocol, streaming,
    MCP dispatch, session resume/fork — all to ClaudeSDKClient.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        mcp_config: str | None = None,
        permission_mode: str = "bypassPermissions",
        extra_args: list[str] | None = None,
        mcp_servers: dict[str, Any] | None = None,
        permission_handler: Callable[[dict], Awaitable[bool]] | None = None,
        disallowed_tools: list[str] | None = None,
        on_event: Callable[["Event"], Awaitable[None]] | None = None,
    ):
        self.model = model
        self.mcp_config = mcp_config
        self.permission_mode = permission_mode
        self.extra_args: list[str] = extra_args or []
        self._mcp_servers: dict[str, Any] = mcp_servers or {}
        self._permission_handler = permission_handler
        self._disallowed_tools: list[str] = disallowed_tools or []
        self._on_event = on_event

        self._state = ClaudeState.IDLE
        self._client: ClaudeSDKClient | None = None
        self._drain_task: asyncio.Task | None = None
        self._session_id: str | None = None
        self._system_prompt_file: Path | None = None
        self._assembled_system_prompt: str = ""

        # Token accounting — populated from SDK ResultMessage.usage
        self._token_count: int = 0
        self._context_window: int = 1_000_000
        self._input_tokens: int = 0
        self._output_tokens: int = 0
        self._cache_creation_tokens: int = 0
        self._cache_read_tokens: int = 0
        self._stop_reason: str | None = None
        self._response_model: str | None = None
        self._response_id: str | None = None

        # Ready latch
        self._ready = asyncio.Event()
        self._ready.set()

        # Lifecycle lock
        self._lifecycle_lock = asyncio.Lock()

        # Reap timer
        self._reap_timeout: int = int(os.environ.get("_ALPHA_REAP_TIMEOUT", "3600"))
        self._reap_task: asyncio.Task | None = None
        self._on_reap: Callable[[], Awaitable[None]] | None = None

    # -- Properties -----------------------------------------------------------

    @property
    def state(self) -> ClaudeState:
        return self._state

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    async def wait_until_ready(self) -> None:
        """Block until Claude finishes current work."""
        await self._ready.wait()

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def pid(self) -> int | None:
        return None  # SDK manages subprocess internally

    @property
    def token_count(self) -> int:
        return self._token_count

    @property
    def context_window(self) -> int:
        return self._context_window

    @property
    def input_tokens(self) -> int:
        return self._input_tokens

    @property
    def total_input_tokens(self) -> int:
        return self._input_tokens + self._cache_creation_tokens + self._cache_read_tokens

    @property
    def cache_creation_tokens(self) -> int:
        return self._cache_creation_tokens

    @property
    def cache_read_tokens(self) -> int:
        return self._cache_read_tokens

    @property
    def output_tokens(self) -> int:
        return self._output_tokens

    @property
    def stop_reason(self) -> str | None:
        return self._stop_reason

    @property
    def response_model(self) -> str | None:
        return self._response_model

    @property
    def response_id(self) -> str | None:
        return self._response_id

    def reset_token_count(self) -> None:
        self._token_count = 0
        self._input_tokens = 0
        self._cache_creation_tokens = 0
        self._cache_read_tokens = 0

    def reset_output_tokens(self) -> None:
        self._output_tokens = 0

    # -- Lifecycle ------------------------------------------------------------

    async def start(self, session_id: str | None = None, fork: bool = False) -> None:
        """Start Claude via ClaudeSDKClient.

        Args:
            session_id: Resume this session, or None for a new session.
            fork: If True (with session_id), fork the session.
        """
        if self._state != ClaudeState.IDLE:
            raise RuntimeError(f"Cannot start in state {self._state}")

        self._state = ClaudeState.STARTING
        self._session_id = session_id
        fork = fork and bool(session_id)
        mode = "fork" if fork else ("resume" if session_id else "fresh")
        logfire.info(
            "claude.lifecycle: start {mode} session={session}",
            mode=mode,
            session=session_id or "(new)",
        )

        try:
            # Assemble system prompt → temp file
            from alpha_app.system_prompt import assemble_system_prompt
            self._assembled_system_prompt = await assemble_system_prompt()
            fd, path = tempfile.mkstemp(suffix=".md", prefix="alpha-sysprompt-")
            with os.fdopen(fd, "w") as f:
                f.write(self._assembled_system_prompt)
            self._system_prompt_file = Path(path)

            logfire.info(
                "system prompt assembled ({n} chars)",
                n=len(self._assembled_system_prompt),
            )

            # Build SDK options
            from alpha_app.constants import CLAUDE_CWD, CLAUDE_CONFIG_DIR

            # MCP servers are now SDK McpSdkServerConfig objects from
            # create_sdk_mcp_server(). Pass them through directly.
            sdk_mcp_servers = dict(self._mcp_servers)

            # Blank ANTHROPIC_API_KEY so the subprocess uses OAuth
            # (CLAUDE_CODE_OAUTH_TOKEN) instead of the API key. The API key
            # exists in our environment for token counting only — it must not
            # leak to Claude Code, which would bill the API account instead
            # of the Max subscription. Auth precedence: API key (#3) beats
            # OAuth token (#5).
            options = ClaudeAgentOptions(
                model=self.model,
                system_prompt={"type": "file", "path": str(self._system_prompt_file)},
                permission_mode=self.permission_mode,
                mcp_servers=sdk_mcp_servers if sdk_mcp_servers else None,
                disallowed_tools=self._disallowed_tools or None,
                include_partial_messages=True,
                cwd=str(CLAUDE_CWD),
                resume=session_id if session_id and not fork else None,
                fork_session=fork,
                env={"ANTHROPIC_API_KEY": ""},
            )

            # Create and connect the client
            self._client = ClaudeSDKClient(options=options)
            await self._client.connect()

            self._state = ClaudeState.RUNNING
            logfire.info(
                "claude.lifecycle: running {mode} session={session}",
                mode=mode,
                session=self._session_id or "?",
            )

            # Start continuous message drain
            if self._on_event:
                self._drain_task = asyncio.create_task(self._drain_messages())

            # Start the idle reap timer
            self._start_reap_timer()

        except Exception as exc:
            logfire.error(
                "claude.lifecycle: start FAILED {mode} session={session} error={error}",
                mode=mode,
                session=session_id or "(new)",
                error=str(exc),
            )
            self._state = ClaudeState.STOPPED
            await self._cleanup()
            raise

    async def send(self, content: list[dict]) -> None:
        """Send a user message to Claude.

        Args:
            content: Messages API content blocks.
        """
        async with self._lifecycle_lock:
            if self._state not in (ClaudeState.RUNNING, ClaudeState.READY):
                raise RuntimeError(f"Cannot send in state {self._state}")

            self._ready.clear()
            self._start_reap_timer()

            # Send as structured content via async iterable (public API)
            async def _one_message():
                yield {
                    "type": "user",
                    "message": {"role": "user", "content": content},
                }

            await self._client.query(_one_message())

    async def stop(self) -> None:
        """Gracefully shut down."""
        self._cancel_reap_timer()

        async with self._lifecycle_lock:
            if self._state == ClaudeState.STOPPED:
                return

            logfire.info(
                "claude.lifecycle: stop session={session}",
                session=self._session_id or "?",
            )
            self._state = ClaudeState.STOPPED
        await self._cleanup()

    # -- Message drain (SDK → our Event types) --------------------------------

    async def _drain_messages(self) -> None:
        """Continuously read from ClaudeSDKClient, map to our events."""
        try:
            async for message in self._client.receive_messages():
                event = self._map_sdk_message(message)
                if event is None:
                    continue

                # Ready latch
                if isinstance(event, SystemEvent) and event.subtype == "init":
                    self._ready.clear()
                elif isinstance(event, ResultEvent):
                    self._ready.set()
                    # Capture session ID
                    if event.session_id and not self._session_id:
                        self._session_id = event.session_id
                    # Update token accounting
                    self._update_usage_from_result(message)

                # Fire the callback
                if self._on_event:
                    await self._on_event(event)

            # Iterator exhausted — subprocess exited or connection lost.
            # Without this block, the drain exits silently: no log, no
            # state change, no _ready.set(), and the frontend sees nothing.
            self._ready.set()
            self._state = ClaudeState.STOPPED
            logfire.warn(
                "claude.lifecycle: receive_messages exhausted session={session}",
                session=self._session_id or "?",
            )
            if self._on_event:
                await self._on_event(
                    ErrorEvent(raw={}, message="Claude process ended unexpectedly")
                )

        except asyncio.CancelledError:
            self._ready.set()
            return
        except Exception as e:
            self._ready.set()
            logfire.error(
                "claude.lifecycle: drain CRASHED session={session} error={error}",
                session=self._session_id or "?",
                error=str(e),
            )
            self._state = ClaudeState.STOPPED

    @staticmethod
    def _trace_enabled() -> bool:
        return os.environ.get("ALPHA_TRACE_CLAUDE_STDIO", "").strip() == "1"

    @staticmethod
    def _trace_streaming_enabled() -> bool:
        return os.environ.get("ALPHA_TRACE_CLAUDE_STDIO_STREAMING", "").strip() == "1"

    def _map_sdk_message(self, message) -> Event | None:
        """Map a claude_agent_sdk message to our Event type."""
        # -- Trace logging (gated by env vars) --
        if self._trace_enabled():
            if isinstance(message, SDKStreamEvent):
                if self._trace_streaming_enabled():
                    inner = message.event if isinstance(message.event, dict) else {}
                    delta_type = inner.get("type", "?")
                    delta_text = ""
                    if delta_type == "content_block_delta":
                        delta = inner.get("delta", {})
                        delta_text = delta.get("text", delta.get("partial_json", ""))[:60]
                    logfire.trace(
                        "claude.stream: {delta_type} {delta_text!r}",
                        delta_type=delta_type,
                        delta_text=delta_text,
                        chars=len(delta_text),
                    )
            elif isinstance(message, SDKAssistantMessage):
                preview = ""
                for block in message.content:
                    if isinstance(block, TextBlock):
                        preview = block.text[:60]
                        break
                logfire.trace(
                    "claude.stdout: assistant {preview}",
                    preview=preview or "(empty)",
                )
            elif isinstance(message, SDKResultMessage):
                logfire.trace(
                    "claude.stdout: result session={session} cost=${cost:.4f} "
                    "turns={turns} error={is_error}",
                    session=message.session_id or "?",
                    cost=message.total_cost_usd or 0.0,
                    turns=message.num_turns or 0,
                    is_error=message.subtype != "success",
                )
            elif isinstance(message, SDKUserMessage):
                logfire.trace("claude.stdout: user-echo")
            elif isinstance(message, SDKSystemMessage):
                logfire.trace(
                    "claude.stdout: system {subtype}",
                    subtype=getattr(message, "subtype", "?"),
                )

        # Log unmapped message types so they don't vanish silently.
        # Also log ResultMessage errors explicitly.
        if isinstance(message, SDKResultMessage) and message.subtype != "success":
            logfire.warn(
                "claude.sdk: error result subtype={subtype} is_error={is_error} "
                "errors={errors}",
                subtype=message.subtype,
                is_error=getattr(message, "is_error", None),
                errors=getattr(message, "errors", None),
            )
        if isinstance(message, SDKAssistantMessage) and getattr(message, "error", None):
            logfire.warn(
                "claude.sdk: assistant error={error}",
                error=str(message.error),
            )

        if isinstance(message, SDKSystemMessage):
            subtype = getattr(message, "subtype", "")
            return SystemEvent(raw={}, subtype=subtype)

        elif isinstance(message, SDKStreamEvent):
            inner = message.event if isinstance(message.event, dict) else {}
            return StreamEvent(raw={}, inner=inner)

        elif isinstance(message, SDKAssistantMessage):
            content = []
            for block in message.content:
                if isinstance(block, TextBlock):
                    content.append({"type": "text", "text": block.text})
                elif hasattr(block, "type"):
                    # tool_use blocks etc.
                    if hasattr(block, "__dict__"):
                        content.append(vars(block))
                    else:
                        content.append({"type": getattr(block, "type", "unknown")})
            return AssistantEvent(
                raw={}, content=content,
                error=getattr(message, "error", None),
            )

        elif isinstance(message, SDKUserMessage):
            content = []
            if hasattr(message, "message") and hasattr(message.message, "content"):
                raw_content = message.message.content
                if isinstance(raw_content, str):
                    content = [{"type": "text", "text": raw_content}]
                elif isinstance(raw_content, list):
                    for block in raw_content:
                        if hasattr(block, "__dict__"):
                            content.append(vars(block))
                        elif isinstance(block, dict):
                            content.append(block)
            return UserEvent(raw={}, content=content)

        elif isinstance(message, SDKResultMessage):
            return ResultEvent(
                raw={},
                session_id=message.session_id or "",
                cost_usd=message.total_cost_usd or 0.0,
                num_turns=message.num_turns or 0,
                duration_ms=int((message.duration_ms or 0)),
                is_error=message.subtype != "success",
            )

        # Unknown message type — log it so we notice
        logfire.debug(
            "claude.sdk: unmapped message type={type}",
            type=type(message).__name__,
        )
        return None

    def _update_usage_from_result(self, message) -> None:
        """Extract token usage from SDK ResultMessage."""
        if not isinstance(message, SDKResultMessage):
            return
        if message.usage:
            self._input_tokens = message.usage.get("input_tokens", 0)
            self._output_tokens += message.usage.get("output_tokens", 0)
            self._cache_creation_tokens = message.usage.get("cache_creation_input_tokens", 0)
            self._cache_read_tokens = message.usage.get("cache_read_input_tokens", 0)
            self._token_count = self.total_input_tokens

    # -- Reap timer -----------------------------------------------------------

    def _start_reap_timer(self) -> None:
        self._cancel_reap_timer()
        self._reap_task = asyncio.create_task(self._reap_after(self._reap_timeout))

    def _cancel_reap_timer(self) -> None:
        if self._reap_task:
            if self._reap_task is not asyncio.current_task():
                self._reap_task.cancel()
            self._reap_task = None

    async def _reap_after(self, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
            await self.stop()
            if self._on_reap:
                await self._on_reap()
        except asyncio.CancelledError:
            pass

    # -- Cleanup --------------------------------------------------------------

    async def _cleanup(self) -> None:
        """Clean up SDK client and temp files."""
        if self._drain_task:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
            self._drain_task = None

        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None

        if self._system_prompt_file and self._system_prompt_file.exists():
            self._system_prompt_file.unlink(missing_ok=True)
            self._system_prompt_file = None

        self._ready.set()

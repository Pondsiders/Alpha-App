"""claude.py — The Claude class. One subprocess, four I/O channels.

The only stateful object in the SDK. Wraps the claude binary over
newline-delimited JSON stdio, with an HTTP proxy for token counting.

Usage:
    claude = Claude(system_prompt="You are a frog.")
    await claude.start()            # New session
    await claude.start("abc-123")   # Resume session
    await claude.send([{"type": "text", "text": "Hello!"}])
    async for event in claude.events():
        if isinstance(event, AssistantEvent):
            print(event.text)
        elif isinstance(event, ResultEvent):
            break
    await claude.stop()
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

import logfire
from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    ListResourcesRequest,
    ListResourceTemplatesRequest,
    ListToolsRequest,
    ReadResourceRequest,
    ReadResourceRequestParams,
)

from .proxy import _Proxy

# -- Subprocess I/O tracing ---------------------------------------------------
# Two-tier gate for Logfire trace-level logging of Claude stdin/stdout.
#   ALPHA_TRACE_CLAUDE_STDIO=1        → log all stdin/stdout events
#   ALPHA_TRACE_CLAUDE_STDIO_STREAMING=1 → also log stream_event deltas (noisy)
# Both require LOGFIRE_MIN_LEVEL=trace to actually reach the dashboard.
# Read lazily (not at import time) so load_dotenv has a chance to run first.


def _trace_stdio_enabled() -> bool:
    return os.environ.get("ALPHA_TRACE_CLAUDE_STDIO", "").strip() == "1"


def _trace_streaming_enabled() -> bool:
    return os.environ.get("ALPHA_TRACE_CLAUDE_STDIO_STREAMING", "").strip() == "1"


def _preview_content(content: list[dict], max_len: int = 60) -> str:
    """Extract a short text preview from Messages API content blocks."""
    texts = []
    for block in content:
        if block.get("type") == "text":
            texts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            texts.append(f"[tool:{block.get('name', '?')}]")
        elif block.get("type") == "tool_result":
            texts.append(f"[result:{block.get('tool_use_id', '?')[:8]}]")
        elif block.get("type") == "image":
            texts.append("[image]")
    combined = " ".join(texts).strip()
    if len(combined) > max_len:
        return combined[:max_len] + "…"
    return combined or "(empty)"


def _trace_stdin(raw: dict) -> None:
    """Log a stdin event with a human-readable span name."""
    if not _trace_stdio_enabled():
        return

    msg_type = raw.get("type", "?")

    if msg_type == "user":
        content = raw.get("message", {}).get("content", [])
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        preview = _preview_content(content)
        logfire.trace("claude.stdin: human {preview}", preview=preview, raw_json=raw)

    elif msg_type == "control_request":
        subtype = raw.get("request", {}).get("subtype", "?")
        logfire.trace("claude.stdin: control {subtype}", subtype=subtype, raw_json=raw)

    elif msg_type == "control_response":
        subtype = raw.get("response", {}).get("subtype", "?")
        logfire.trace("claude.stdin: response {subtype}", subtype=subtype, raw_json=raw)

    else:
        logfire.trace("claude.stdin: {msg_type}", msg_type=msg_type, raw_json=raw)


def _trace_stdout(raw: dict) -> None:
    """Log a stdout event with a human-readable span name."""
    if not _trace_stdio_enabled():
        return

    msg_type = raw.get("type", "?")

    if msg_type == "stream_event":
        if not _trace_streaming_enabled():
            return
        inner = raw.get("event", {})
        event_type = inner.get("type", "?")
        delta_type = inner.get("delta", {}).get("type", "")
        text = inner.get("delta", {}).get("text", "") or inner.get("delta", {}).get("thinking", "")
        preview = (text[:40] + "…") if len(text) > 40 else text
        logfire.trace(
            "claude.stdout: stream {event_type} {delta_type} {preview}",
            event_type=event_type, delta_type=delta_type, preview=preview,
            raw_json=raw,
        )

    elif msg_type == "assistant":
        content = raw.get("message", {}).get("content", [])
        preview = _preview_content(content)
        logfire.trace("claude.stdout: assistant {preview}", preview=preview, raw_json=raw)

    elif msg_type == "user":
        content = raw.get("message", {}).get("content", [])
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        preview = _preview_content(content)
        logfire.trace("claude.stdout: user-echo {preview}", preview=preview, raw_json=raw)

    elif msg_type == "result":
        session = raw.get("session_id", "?")[:8]
        cost = raw.get("total_cost_usd", 0)
        turns = raw.get("num_turns", 0)
        is_error = raw.get("is_error", False)
        logfire.trace(
            "claude.stdout: result session={session} cost=${cost:.4f} turns={turns} error={is_error}",
            session=session, cost=cost, turns=turns, is_error=is_error,
            raw_json=raw,
        )

    elif msg_type == "system":
        subtype = raw.get("subtype", "?")
        logfire.trace("claude.stdout: system {subtype}", subtype=subtype, raw_json=raw)

    elif msg_type == "control_request":
        subtype = raw.get("request", {}).get("subtype", "?")
        tool = raw.get("request", {}).get("tool_name", "")
        label = f"{subtype}:{tool}" if tool else subtype
        logfire.trace("claude.stdout: control {label}", label=label, raw_json=raw)

    elif msg_type == "control_response":
        # Init response — show model name
        model = raw.get("response", {}).get("model", "?")
        logfire.trace("claude.stdout: init model={model}", model=model, raw_json=raw)

    else:
        logfire.trace("claude.stdout: {msg_type}", msg_type=msg_type, raw_json=raw)


def _bundled_claude_path() -> str:
    """Resolve the claude binary bundled inside claude-agent-sdk."""
    try:
        import claude_agent_sdk._bundled as _bundled
    except ImportError:
        raise RuntimeError(
            "claude-agent-sdk is not installed. "
            "alpha_app requires claude-agent-sdk — install it or "
            "check that your environment has the right dependencies."
        )

    bundled_dir = Path(_bundled.__path__[0])
    binary = bundled_dir / "claude"

    if not binary.exists():
        raise RuntimeError(
            f"Bundled claude binary not found at {binary}. "
            f"claude-agent-sdk may be corrupt — reinstall it."
        )

    return str(binary)


# -- State machine ------------------------------------------------------------


class ClaudeState(Enum):
    """Lifecycle states for the claude subprocess."""

    IDLE = auto()      # Not started
    STARTING = auto()  # Subprocess spawned, init handshake in progress
    RUNNING = auto()   # Init complete, drain running, accepting messages
    READY = RUNNING    # Backward compat alias — will be removed
    STOPPED = auto()   # Shut down (graceful or error)


# -- Events -------------------------------------------------------------------


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
    """User message — content blocks.

    Emitted during replay (session history) and when --replay-user-messages
    is enabled (interjection echo — the turn boundary signal).
    """

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
    """System message from claude (session_id, etc.)."""

    subtype: str = ""


@dataclass
class ErrorEvent(Event):
    """Error from claude (process death, parse failure, etc.)."""

    message: str = ""


@dataclass
class StreamEvent(Event):
    """Streaming delta — Messages API format wrapped by claude.

    Arrives BEFORE the complete AssistantEvent for the same content.
    """

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


# Internal — not yielded to consumers
@dataclass
class _ControlRequestEvent(Event):
    """Control request from claude (MCP messages, permission requests, etc.)."""

    request_id: str = ""
    tool_name: str = ""
    request: dict = field(default_factory=dict)


# -- Replay -------------------------------------------------------------------


def _find_session_path(session_id: str, sessions_dir: Path | None = None) -> Path:
    """Locate a session's JSONL transcript file."""
    if sessions_dir:
        path = sessions_dir / f"{session_id}.jsonl"
    else:
        # Default: ~/.claude/projects/{cwd-with-dashes}/
        cwd = os.path.realpath(os.getcwd()).replace("/", "-")
        path = Path.home() / ".claude" / "projects" / cwd / f"{session_id}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"Session transcript not found: {path}")
    return path


async def replay_session(
    session_id: str,
    sessions_dir: Path | None = None,
) -> AsyncIterator[Event]:
    """Read a session's JSONL transcript and yield replay events.

    Yields UserEvent and AssistantEvent with is_replay=True.
    No StreamEvents (those are real-time only), no control events.
    Skips queue-operation and system records.
    """
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
            # Normalize content to list of blocks
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            yield UserEvent(raw=record, content=content, is_replay=True)
        elif record_type == "assistant":
            if isinstance(content, list):
                yield AssistantEvent(raw=record, content=content, is_replay=True)


# -- Claude -------------------------------------------------------------------


class Claude:
    """A claude subprocess. The only stateful object in the SDK.

    Manages four I/O channels:
    - stdin/stdout: JSON message framing
    - stderr: background drain
    - HTTP: localhost proxy for token counting

    Simple API: start(), send(), events(), stop().
    No queues, no routers, no sessions — just the subprocess.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        system_prompt: str | None = None,
        mcp_config: str | None = None,
        permission_mode: str = "bypassPermissions",
        extra_args: list[str] | None = None,
        mcp_servers: dict[str, Any] | None = None,
        permission_handler: Callable[[dict], Awaitable[bool]] | None = None,
        disallowed_tools: list[str] | None = None,
        on_event: Callable[["Event"], Awaitable[None]] | None = None,
        use_proxy: bool = True,
    ):
        self.model = model
        self.system_prompt = system_prompt
        self.mcp_config = mcp_config
        self.permission_mode = permission_mode
        self.extra_args: list[str] = extra_args or []
        self._mcp_servers: dict[str, Any] = mcp_servers or {}
        self._permission_handler = permission_handler
        self._disallowed_tools: list[str] = disallowed_tools or []
        self._on_event = on_event
        self._use_proxy = use_proxy

        self._state = ClaudeState.IDLE
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stdout_task: asyncio.Task | None = None
        self._proxy: _Proxy | None = None
        self._session_id: str | None = None
        self._system_prompt_file: Path | None = None  # Temp file for large system prompts
        self._trace_context: dict | None = None  # Turn span context for stdout traces

    @property
    def state(self) -> ClaudeState:
        return self._state

    @property
    def session_id(self) -> str | None:
        """Session ID discovered during init or provided at start."""
        return self._session_id

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    # -- Proxy delegates ------------------------------------------------------

    @property
    def token_count(self) -> int:
        """Current token count from the HTTP proxy. 0 if no proxy."""
        return self._proxy.token_count if self._proxy else 0

    @property
    def context_window(self) -> int:
        """Context window size. Default if no proxy."""
        return self._proxy.context_window if self._proxy else _Proxy.DEFAULT_CONTEXT_WINDOW

    @property
    def usage_7d(self) -> float | None:
        return self._proxy.usage_7d if self._proxy else None

    @property
    def usage_5h(self) -> float | None:
        return self._proxy.usage_5h if self._proxy else None

    @property
    def input_tokens(self) -> int:
        return self._proxy.input_tokens if self._proxy else 0

    @property
    def total_input_tokens(self) -> int:
        """OTel-compliant total: uncached + cache_creation + cache_read."""
        return self._proxy.total_input_tokens if self._proxy else 0

    @property
    def cache_creation_tokens(self) -> int:
        return self._proxy.cache_creation_tokens if self._proxy else 0

    @property
    def cache_read_tokens(self) -> int:
        return self._proxy.cache_read_tokens if self._proxy else 0

    @property
    def output_tokens(self) -> int:
        return self._proxy.output_tokens if self._proxy else 0

    @property
    def stop_reason(self) -> str | None:
        return self._proxy.stop_reason if self._proxy else None

    @property
    def response_model(self) -> str | None:
        return self._proxy.response_model if self._proxy else None

    @property
    def response_id(self) -> str | None:
        return self._proxy.response_id if self._proxy else None

    def reset_token_count(self) -> None:
        """Reset token count to 0. Call after compaction."""
        if self._proxy:
            self._proxy.reset_token_count()

    def reset_output_tokens(self) -> None:
        """Reset just the output token accumulator. Call at turn start."""
        if self._proxy:
            self._proxy.reset_output_tokens()

    def set_trace_context(self, ctx: dict | None) -> None:
        """Set trace context so proxy and stdout traces nest under the turn span.

        Call with logfire.get_context() before each turn.
        Clear (call with None) when the turn span closes.
        """
        self._trace_context = ctx
        if self._proxy:
            self._proxy.set_trace_context(ctx)

    # -- Lifecycle ------------------------------------------------------------

    async def start(self, session_id: str | None = None) -> None:
        """Spawn claude and perform the init handshake.

        Args:
            session_id: Resume this session, or None for a new session.
        """
        if self._state != ClaudeState.IDLE:
            raise RuntimeError(f"Cannot start in state {self._state}")

        self._state = ClaudeState.STARTING
        self._session_id = session_id
        mode = "resume" if session_id else "fresh"
        logfire.info(
            "claude.lifecycle: start {mode} session={session}",
            mode=mode,
            session=session_id or "(new)",
        )

        try:
            if self._use_proxy:
                upstream_url = os.environ.get("ANTHROPIC_BASE_URL")
                self._proxy = _Proxy(upstream_url=upstream_url)
                await self._proxy.start()

            self._proc = await self._spawn()
            self._stderr_task = asyncio.create_task(self._drain_stderr())
            await self._init_handshake()
            self._state = ClaudeState.RUNNING
            logfire.info(
                "claude.lifecycle: running {mode} session={session} pid={pid}",
                mode=mode,
                session=self._session_id or "?",
                pid=self._proc.pid if self._proc else "?",
            )

            # Start continuous stdout drain AFTER the init handshake.
            # From this point on, _drain_stdout owns the stdout pipe.
            # events() is dead — all events flow through on_event callback.
            if self._on_event:
                self._stdout_task = asyncio.create_task(self._drain_stdout())
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
        """Send a user message to claude.

        Always works after start(). Claude Code handles internal queueing —
        messages sent while it's busy get processed in order.

        Args:
            content: Messages API content blocks.
        """
        if self._state not in (ClaudeState.RUNNING, ClaudeState.READY):
            raise RuntimeError(f"Cannot send in state {self._state}")

        await self._send_json(self._format_user_message(content))

    async def _drain_stdout(self) -> None:
        """Continuously read stdout, handle control requests, emit events.

        Runs as a background task for the lifetime of the subprocess.
        This is the ONLY reader of stdout — all events flow through
        the on_event callback. No queue, no consumer, no events() generator.
        """
        try:
            while True:
                raw = await self._read_json()

                if raw is None:
                    # Subprocess exited
                    self._state = ClaudeState.STOPPED
                    logfire.warn(
                        "claude.lifecycle: subprocess exited session={session}",
                        session=self._session_id or "?",
                    )
                    if self._on_event:
                        await self._on_event(
                            ErrorEvent(raw={}, message="claude process exited unexpectedly")
                        )
                    return

                event = self._parse_event(raw)

                # Attach the turn's trace context so ALL traces in this
                # iteration (stdout trace, broadcast traces from the callback,
                # etc.) nest under alpha.turn in Logfire. Between turns,
                # _trace_context is None and traces float as top-level (correct).
                ctx = (
                    logfire.attach_context(self._trace_context)
                    if self._trace_context
                    else contextlib.nullcontext()
                )
                with ctx:
                    _trace_stdout(raw)

                    # Control requests are internal — handle and don't emit.
                    if isinstance(event, _ControlRequestEvent):
                        await self._handle_control_request(event)
                        continue

                    # Capture session ID from first ResultEvent.
                    if isinstance(event, ResultEvent) and not self._session_id:
                        if event.session_id:
                            self._session_id = event.session_id

                    # Fire the callback — this is where events reach Chat and
                    # ultimately the WebSocket handler.
                    if self._on_event:
                        await self._on_event(event)

        except asyncio.CancelledError:
            logfire.debug(
                "claude.lifecycle: drain cancelled session={session}",
                session=self._session_id or "?",
            )
            return
        except Exception as e:
            logfire.error(
                "claude.lifecycle: drain CRASHED session={session} error={error}",
                session=self._session_id or "?",
                error=str(e),
            )
            self._state = ClaudeState.STOPPED

    async def events(self) -> AsyncIterator[Event]:
        """DEPRECATED: Use the on_event callback instead.

        Kept temporarily for backward compatibility during migration.
        Only works when on_event is NOT set (old pull-based mode).
        """
        if self._on_event:
            raise RuntimeError(
                "Cannot use events() when on_event callback is set. "
                "Events flow through the callback, not the generator."
            )

        if self._state not in (ClaudeState.RUNNING, ClaudeState.READY):
            raise RuntimeError(f"Cannot read events in state {self._state}")

        while True:
            raw = await self._read_json()

            if raw is None:
                self._state = ClaudeState.STOPPED
                yield ErrorEvent(raw={}, message="claude process exited unexpectedly")
                return

            event = self._parse_event(raw)
            _trace_stdout(raw)

            if isinstance(event, _ControlRequestEvent):
                await self._handle_control_request(event)
                continue

            if isinstance(event, ResultEvent) and not self._session_id:
                if event.session_id:
                    self._session_id = event.session_id

            yield event

            if isinstance(event, ResultEvent):
                return

    async def stop(self) -> None:
        """Gracefully shut down the claude process."""
        if self._state == ClaudeState.STOPPED:
            return

        logfire.info(
            "claude.lifecycle: stop session={session}",
            session=self._session_id or "?",
        )
        self._state = ClaudeState.STOPPED
        await self._cleanup()

    # -- Protocol helpers (static, unit-testable) -----------------------------

    @staticmethod
    def _format_user_message(content: list[dict]) -> dict:
        return {
            "type": "user",
            "session_id": "",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
        }

    @staticmethod
    def _format_init_request() -> dict:
        return {
            "type": "control_request",
            "request_id": f"req_0_{os.urandom(4).hex()}",
            "request": {
                "subtype": "initialize",
                "hooks": {},
                "agents": {},
            },
        }

    @staticmethod
    def _format_permission_response(request_id: str) -> dict:
        return {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": request_id,
                "response": {"approved": True},
            },
        }

    @staticmethod
    def _parse_event(raw: dict) -> Event:
        """Parse a raw JSON dict into a typed Event."""
        msg_type = raw.get("type", "")

        if msg_type == "assistant":
            content = raw.get("message", {}).get("content", [])
            return AssistantEvent(raw=raw, content=content)

        elif msg_type == "result":
            return ResultEvent(
                raw=raw,
                session_id=raw.get("session_id", ""),
                cost_usd=raw.get("total_cost_usd", 0.0),
                num_turns=raw.get("num_turns", 0),
                duration_ms=raw.get("duration_ms", 0),
                is_error=raw.get("is_error", False),
            )

        elif msg_type == "user":
            content = raw.get("message", {}).get("content", [])
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            return UserEvent(raw=raw, content=content)

        elif msg_type == "system":
            return SystemEvent(raw=raw, subtype=raw.get("subtype", ""))

        elif msg_type == "control_request":
            req = raw.get("request", {})
            return _ControlRequestEvent(
                raw=raw,
                request_id=raw.get("request_id", ""),
                tool_name=req.get("tool_name", req.get("subtype", "")),
                request=req,
            )

        elif msg_type == "control_response":
            resp = raw.get("response", {})
            return InitEvent(
                raw=raw,
                model=resp.get("model", ""),
                tools=resp.get("tools", []),
                mcp_servers=resp.get("mcpServers", []),
            )

        elif msg_type == "stream_event":
            inner = raw.get("event", {})
            return StreamEvent(raw=raw, inner=inner)

        else:
            return Event(raw=raw)

    # -- MCP dispatch ---------------------------------------------------------

    def _build_mcp_config(self) -> str | None:
        """Build merged MCP config for --mcp-config flag.

        Merges consumer's external MCP config with SDK's in-process
        servers. Consumer config can be inline JSON or a file path.
        SDK servers use type "sdk" so claude routes them back to us.
        """
        merged: dict[str, dict] = {}

        # Consumer config — inline JSON or file path
        if self.mcp_config:
            try:
                consumer = json.loads(self.mcp_config)
                merged.update(consumer.get("mcpServers", {}))
            except json.JSONDecodeError:
                config_path = Path(self.mcp_config)
                if config_path.exists():
                    with open(config_path) as f:
                        consumer = json.load(f)
                    merged.update(consumer.get("mcpServers", {}))

        # SDK in-process servers — type "sdk" routes back to us
        for name in self._mcp_servers:
            merged[name] = {"type": "sdk", "name": name}

        if not merged:
            return None

        return json.dumps({"mcpServers": merged})

    async def _dispatch_mcp(self, server_name: str, mcp_msg: dict) -> dict:
        """Dispatch an MCP JSON-RPC message to an in-process FastMCP server.

        Routes by method, calls request_handlers directly. No transport
        layer — just dict in, dict out. Copied from quack-mcp.py which
        copied from the Agent SDK's query.py.
        """
        server_instance = self._mcp_servers.get(server_name)
        if not server_instance:
            return {
                "jsonrpc": "2.0",
                "id": mcp_msg.get("id"),
                "error": {
                    "code": -32601,
                    "message": f"Unknown SDK MCP server: {server_name}",
                },
            }

        low_level = server_instance._mcp_server
        method = mcp_msg.get("method")
        params = mcp_msg.get("params", {})
        msg_id = mcp_msg.get("id")

        try:
            if method == "initialize":
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}, "resources": {}},
                        "serverInfo": {
                            "name": server_instance.name,
                            "version": "1.0.0",
                        },
                    },
                }

            elif method == "notifications/initialized":
                return {"jsonrpc": "2.0", "result": {}}

            elif method == "tools/list":
                request = ListToolsRequest(method=method)
                handler = low_level.request_handlers.get(ListToolsRequest)
                if not handler:
                    raise Exception("No tools/list handler registered")
                result = await handler(request)
                tools_data = [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": (
                            tool.inputSchema.model_dump()
                            if hasattr(tool.inputSchema, "model_dump")
                            else tool.inputSchema
                        )
                        if tool.inputSchema
                        else {},
                    }
                    for tool in result.root.tools
                ]
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"tools": tools_data},
                }

            elif method == "tools/call":
                call_request = CallToolRequest(
                    method=method,
                    params=CallToolRequestParams(
                        name=params.get("name"),
                        arguments=params.get("arguments", {}),
                    ),
                )
                handler = low_level.request_handlers.get(CallToolRequest)
                if not handler:
                    raise Exception("No tools/call handler registered")
                result = await handler(call_request)
                content = []
                for item in result.root.content:
                    if hasattr(item, "text"):
                        content.append({"type": "text", "text": item.text})
                    elif hasattr(item, "data") and hasattr(item, "mimeType"):
                        content.append({
                            "type": "image",
                            "data": item.data,
                            "mimeType": item.mimeType,
                        })
                response_data: dict = {"content": content}
                if hasattr(result.root, "is_error") and result.root.is_error:
                    response_data["is_error"] = True
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": response_data,
                }

            elif method == "resources/list":
                handler = low_level.request_handlers.get(ListResourcesRequest)
                if not handler:
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {"resources": []},
                    }
                result = await handler(ListResourcesRequest(method=method))
                resources_data = [
                    {
                        "uri": str(r.uri),
                        "name": r.name or "",
                        "description": r.description or "",
                        "mimeType": r.mimeType or "text/plain",
                    }
                    for r in result.root.resources
                ]
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"resources": resources_data},
                }

            elif method == "resources/templates/list":
                handler = low_level.request_handlers.get(ListResourceTemplatesRequest)
                if not handler:
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {"resourceTemplates": []},
                    }
                result = await handler(ListResourceTemplatesRequest(method=method))
                templates_data = [
                    {
                        "uriTemplate": str(t.uriTemplate),
                        "name": t.name or "",
                        "description": t.description or "",
                        "mimeType": t.mimeType or "text/plain",
                    }
                    for t in result.root.resourceTemplates
                ]
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"resourceTemplates": templates_data},
                }

            elif method == "resources/read":
                handler = low_level.request_handlers.get(ReadResourceRequest)
                if not handler:
                    raise Exception("No resources/read handler registered")
                result = await handler(ReadResourceRequest(
                    method=method,
                    params=ReadResourceRequestParams(
                        uri=params.get("uri"),
                    ),
                ))
                contents = []
                for item in result.root.contents:
                    content_item = {"uri": str(item.uri)}
                    if hasattr(item, "text") and item.text is not None:
                        content_item["text"] = item.text
                        content_item["mimeType"] = getattr(item, "mimeType", "text/plain")
                    elif hasattr(item, "blob") and item.blob is not None:
                        content_item["blob"] = item.blob
                        content_item["mimeType"] = getattr(item, "mimeType", "application/octet-stream")
                    contents.append(content_item)
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"contents": contents},
                }

            else:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method '{method}' not found",
                    },
                }

        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": str(e)},
            }

    async def _handle_control_request(self, event: _ControlRequestEvent) -> None:
        """Handle a control_request — MCP dispatch or permission request.

        Three-way split:
        - MCP message → dispatch to in-process FastMCP server
        - Permission request + handler → delegate to consumer callback
        - Permission request + no handler → RuntimeError (fail loud)
        """
        req = event.request
        subtype = req.get("subtype", "")

        if subtype == "mcp_message":
            server_name = req.get("server_name", "")
            mcp_msg = req.get("message", {})
            mcp_response = await self._dispatch_mcp(server_name, mcp_msg)
            await self._send_json({
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": event.request_id,
                    "response": {"mcp_response": mcp_response},
                },
            })
        elif self._permission_handler:
            approved = await self._permission_handler(req)
            await self._send_json({
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": event.request_id,
                    "response": {"approved": approved},
                },
            })
        else:
            raise RuntimeError(
                f"Permission request received but no permission_handler "
                f"configured. Tool: {event.tool_name}, subtype: {subtype}"
            )

    # -- Subprocess management ------------------------------------------------

    async def _spawn(self) -> asyncio.subprocess.Process:
        """Spawn the claude binary with stream-json protocol."""
        cmd = [
            _bundled_claude_path(),
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            "--model", self.model,
            "--permission-mode", self.permission_mode,
            "--include-partial-messages",
            "--replay-user-messages",
            "--effort", "medium",
        ]

        if self.system_prompt is not None:
            # Write system prompt to a temp file to avoid argv size limits.
            # A 50K-token orientation can exceed Linux's MAX_ARG_STRLEN.
            import tempfile
            fd, path = tempfile.mkstemp(suffix=".md", prefix="alpha-sysprompt-")
            with os.fdopen(fd, "w") as f:
                f.write(self.system_prompt)
            self._system_prompt_file = Path(path)
            cmd.extend(["--system-prompt-file", path])

        mcp_config = self._build_mcp_config()
        if mcp_config:
            cmd.extend(["--mcp-config", mcp_config])

        if self._session_id:
            cmd.extend(["--resume", self._session_id])

        if self._disallowed_tools:
            cmd.extend(["--disallowedTools", ",".join(self._disallowed_tools)])

        if self.extra_args:
            cmd.extend(self.extra_args)

        from alpha_app.constants import CLAUDE_CONFIG_DIR, CLAUDE_CWD, JE_NE_SAIS_QUOI

        if JE_NE_SAIS_QUOI.exists():
            cmd.extend(["--plugin-dir", str(JE_NE_SAIS_QUOI)])

        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        env["CLAUDE_CONFIG_DIR"] = str(CLAUDE_CONFIG_DIR)
        if self._proxy and self._proxy.port:
            env["ANTHROPIC_BASE_URL"] = self._proxy.base_url
        elif not self._use_proxy:
            # Remove inherited ANTHROPIC_BASE_URL so the subprocess
            # talks directly to Anthropic, not through a parent's proxy.
            env.pop("ANTHROPIC_BASE_URL", None)

        return await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=1024 * 1024,  # 1MB — default 64KB chokes on large tool results
            env=env,
            cwd=str(CLAUDE_CWD),
        )

    async def _init_handshake(self) -> InitEvent:
        """Perform the init handshake and return capabilities.

        During init, claude sends MCP setup messages (initialize,
        notifications/initialized, tools/list) for each SDK server.
        We dispatch those to our in-process servers while waiting
        for the actual init response.
        """
        await self._send_json(self._format_init_request())

        while True:
            raw = await self._read_json()
            if raw is None:
                raise RuntimeError("claude exited during init handshake")

            event = self._parse_event(raw)

            if isinstance(event, _ControlRequestEvent):
                await self._handle_control_request(event)
                continue

            if isinstance(event, InitEvent):
                return event

    async def _send_json(self, obj: dict) -> None:
        """Send a JSON object to claude's stdin."""
        assert self._proc and self._proc.stdin
        _trace_stdin(obj)
        self._proc.stdin.write((json.dumps(obj) + "\n").encode())
        await self._proc.stdin.drain()

    async def _read_json(self) -> dict | None:
        """Read one JSON object from claude's stdout."""
        assert self._proc and self._proc.stdout
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                return None
            text = line.decode().strip()
            if not text:
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue

    async def _send_permission_response(self, request_id: str) -> None:
        await self._send_json(self._format_permission_response(request_id))

    async def _drain_stderr(self) -> None:
        """Read stderr in the background so it doesn't block."""
        assert self._proc and self._proc.stderr
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                break

    async def _cleanup(self) -> None:
        """Clean up subprocess, background tasks, and proxy."""
        if self._proc:
            if self._proc.stdin and not self._proc.stdin.is_closing():
                self._proc.stdin.close()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()

        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass

        if self._stdout_task and not self._stdout_task.done():
            self._stdout_task.cancel()
            try:
                await self._stdout_task
            except asyncio.CancelledError:
                pass

        if self._proxy:
            await self._proxy.stop()
            self._proxy = None

        if self._system_prompt_file and self._system_prompt_file.exists():
            self._system_prompt_file.unlink(missing_ok=True)
            self._system_prompt_file = None

---
title: Claude
---

# Claude


`alpha_app.claude.Claude` wraps the Claude Code binary over newline-delimited JSON stdio. One subprocess, four I/O channels (stdin, stdout, stderr, HTTP proxy).

## Construction

### `Claude(model, system_prompt, mcp_config, permission_mode, extra_args, mcp_servers, permission_handler, disallowed_tools, on_event, use_proxy)`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | `str` | `"claude-sonnet-4-20250514"` | Model name (overridden by Chat to use `constants.CLAUDE_MODEL`) |
| `system_prompt` | `str \| None` | `None` | Written to a temp file, passed via `--system-prompt-file` |
| `mcp_config` | `str \| None` | `None` | Path to MCP config JSON |
| `permission_mode` | `str` | `"bypassPermissions"` | Claude Code permission mode |
| `extra_args` | `list[str] \| None` | `None` | Additional CLI arguments |
| `mcp_servers` | `dict[str, Any] \| None` | `None` | In-process FastMCP servers (dispatched via control requests) |
| `permission_handler` | `Callable \| None` | `None` | Custom permission handler for tool approvals |
| `disallowed_tools` | `list[str] \| None` | `None` | Tools to deny (passed as `--disallowedTools`) |
| `on_event` | `Callable[[Event], Awaitable[None]] \| None` | `None` | Callback for all events from stdout |
| `use_proxy` | `bool` | `True` | Whether to run the HTTP proxy for token counting |

## Lifecycle

### `async start(session_id=None, fork=False)`

Spawn the claude binary and perform the init handshake.

- `session_id=None` → fresh session
- `session_id="abc"` → resume existing session (`--resume abc`)
- `session_id="abc", fork=True` → fork from session (`--resume abc --fork-session`)

Steps: start proxy (if enabled) → spawn subprocess → drain stderr → init handshake → start stdout drain → start reap timer.

The session ID is not known until the first turn. `start()` stores the provided session ID for `--resume`, but the actual session ID (possibly new, if forking) arrives in the first `ResultEvent`.

### `async send(content: list[dict])`

Send a user message to Claude. Content is a list of Messages API content blocks. Serialized by `_lifecycle_lock` to prevent races with stop/reap. Resets the reap timer on every call.

Can be called while Claude is busy — Claude Code handles internal queueing.

### `async stop()`

Gracefully stop the subprocess. Writes EOF to stdin, waits up to 5 seconds for exit, then kills. Cleans up proxy, temp files, background tasks.

### `async wait_until_ready()`

Block until Claude finishes current work (the `_ready` Event is set). Safe to call when already ready. Used by headless jobs to sequence multi-step operations.

## Properties

### State

| Property | Type | Description |
|----------|------|-------------|
| `state` | `ClaudeState` | `IDLE` / `STARTING` / `RUNNING` / `STOPPED` |
| `is_ready` | `bool` | True when idle and ready for input |
| `session_id` | `str \| None` | Session UUID (populated after first turn) |
| `pid` | `int \| None` | Subprocess PID |

### Token usage (from proxy)

| Property | Type | Description |
|----------|------|-------------|
| `token_count` | `int` | Current context window usage (cumulative input) |
| `context_window` | `int` | Total context window size |
| `input_tokens` | `int` | Uncached input tokens (current turn) |
| `total_input_tokens` | `int` | All input: uncached + cache_creation + cache_read |
| `cache_creation_tokens` | `int` | Prompt cache creation tokens |
| `cache_read_tokens` | `int` | Prompt cache read tokens |
| `output_tokens` | `int` | Output tokens (reset per turn via `reset_output_tokens`) |
| `stop_reason` | `str \| None` | Why Claude stopped (`end_turn`, `tool_use`, etc.) |
| `response_model` | `str \| None` | Model that generated the response |
| `response_id` | `str \| None` | API response ID |
| `usage_5h` | `float \| None` | 5-hour quota usage (from `x-ratelimit-*` headers) |
| `usage_7d` | `float \| None` | 7-day quota usage |

### `reset_output_tokens()`

Reset the output token accumulator. Call at the start of each turn.

### `reset_token_count()`

Reset all token accumulators to zero.

### `set_trace_context(ctx: dict | None)`

Set Logfire trace context so proxy and stdout traces nest under the consumer's turn span.

## Reap timer

Claude self-manages an idle timeout (default 3600s, configurable via `_ALPHA_REAP_TIMEOUT`).

### `_start_reap_timer()`

Start or restart the idle timer. Called on every `send()`.

### `_cancel_reap_timer()`

Cancel the idle timer. Called on `stop()`.

When the timer fires, Claude calls `stop()` on itself and invokes the `_on_reap` callback (set by Chat) so the owner knows the subprocess is gone.

## Event types

All events from Claude's stdout are parsed into typed dataclasses:

### `InitEvent`

Capabilities advertisement from the init handshake. Contains `model`, `tools`, and `mcp_servers`.

### `UserEvent`

User message echo. Emitted during replay (session history) and for interjection echoes (`--replay-user-messages`). Contains `content` (list of content blocks) and a `text` property.

### `AssistantEvent`

Complete assistant response — full content blocks (text, tool_use). Contains `content` and a `text` property. Arrives AFTER all `StreamEvent` deltas for the same content.

### `StreamEvent`

Streaming delta in Messages API format. Properties:

| Property | Type | Description |
|----------|------|-------------|
| `event_type` | `str` | `content_block_start`, `content_block_delta`, etc. |
| `index` | `int` | Content block index |
| `delta_type` | `str` | `text_delta`, `thinking_delta`, `input_json_delta` |
| `delta_text` | `str` | Text content of the delta |
| `delta_partial_json` | `str` | Partial JSON for tool input streaming |
| `block_type` | `str` | Block type on `content_block_start` |
| `block_id` | `str` | Block ID (for tool_use blocks) |
| `block_name` | `str` | Tool name (for tool_use blocks) |

### `ResultEvent`

End-of-turn result. Contains `session_id`, `cost_usd`, `num_turns`, `duration_ms`, `is_error`.

### `SystemEvent`

System message from Claude. Contains `subtype` (`compact_boundary`, `task_started`, `task_progress`, `task_notification`).

### `ErrorEvent`

Error from Claude (process death, parse failure). Contains `message`.

## MCP dispatch

When Claude makes a tool call via MCP, it sends a `control_request` on stdout. Claude's `_handle_control_request` routes these to the in-process FastMCP servers registered in `mcp_servers`.

Supported MCP operations:
- `tools/list` — enumerate available tools
- `tools/call` — execute a tool
- `resources/list` — list available resources
- `resources/templates/list` — list resource templates
- `resources/read` — read a resource

The dispatch is fully async. Tool results are sent back on stdin as `control_response` messages.

## Subprocess details

### `_spawn() -> Process`

Builds the command line:

```
claude --output-format stream-json --input-format stream-json
       --verbose --model {model} --permission-mode {mode}
       --include-partial-messages --replay-user-messages
       --effort medium
       [--system-prompt-file {path}]
       [--mcp-config {path}]
       [--resume {session_id}] [--fork-session]
       [--disallowedTools {tools}]
       [--plugin-dir {JNSQ}]
```

Environment: inherits `os.environ` minus `CLAUDECODE`, sets `CLAUDE_CONFIG_DIR`, overrides `ANTHROPIC_BASE_URL` if proxy is running.

Working directory: `/Pondside` (from `constants.CLAUDE_CWD`).

Buffer limit: 1MB (default 64KB chokes on large tool results).

### `_drain_stdout()`

Background task that continuously reads stdout, parses events, handles control requests (MCP dispatch), and calls `on_event` for everything else. This is the ONLY reader of stdout — all events flow through the callback.

### `_init_handshake()`

Send the init request, dispatch MCP setup messages (`initialize`, `notifications/initialized`, `tools/list` for each server), and wait for the init response with capabilities.

### `_drain_stderr()`

Background task that reads stderr and logs it at debug level. Captures the claude binary's own diagnostic output.

## ClaudeState

| State | Description |
|-------|-------------|
| `IDLE` | Not started |
| `STARTING` | Subprocess spawned, init handshake in progress |
| `RUNNING` | Init complete, drain running, accepting messages |
| `STOPPED` | Shut down (graceful or error) |

## Tracing

Two-tier gate for Logfire trace-level logging:
- `ALPHA_TRACE_CLAUDE_STDIO=1` — log all stdin/stdout events
- `ALPHA_TRACE_CLAUDE_STDIO_STREAMING=1` — also log stream_event deltas (very noisy)

Both require `LOGFIRE_MIN_LEVEL=trace` to actually reach the dashboard.

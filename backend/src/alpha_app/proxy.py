"""proxy.py — HTTP proxy for the claude subprocess's API channel.

Claude manages four I/O channels:
  1. stdin  — JSON messages in
  2. stdout — JSON events out
  3. stderr — diagnostic output (drained in background)
  4. HTTP   — API requests via ANTHROPIC_BASE_URL

This module handles #4. It is a private implementation detail of Claude.
No other module should import this directly.

The proxy sits between claude and Anthropic's API. It:
- Sniffs usage data from streaming SSE events (message_start + message_delta)
- Sniffs usage quota headers from responses
- Captures raw requests for debugging (ALPHA_SDK_CAPTURE_REQUESTS)

Usage extraction works by parsing the Anthropic SSE stream in-flight:
- message_start: input tokens (with cache breakdown), model, response ID
- message_delta: output tokens, stop reason
- Response headers: 5h and 7d quota utilization
Zero extra API calls, zero extra latency.

Compact prompt rewriting was removed on March 22, 2026. At 1M context
tokens, compaction is vestigial — we never approach the limit in a day's
work. The rewriting code lived here from v1.x through the monorepo era
and is preserved in git history.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
from datetime import datetime
from pathlib import Path

import httpx
import logfire
from aiohttp import web

from alpha_app.constants import CONTEXT_WINDOW


ANTHROPIC_API_URL = "https://api.anthropic.com"

# Debug capture mode — dumps raw requests to files
CAPTURE_REQUESTS = os.environ.get(
    "ALPHA_SDK_CAPTURE_REQUESTS", ""
).lower() in ("1", "true", "yes")
CAPTURE_DIR = Path(
    os.environ.get(
        "ALPHA_SDK_CAPTURE_DIR",
        "/Pondside/Workshop/Projects/Alpha-App/api_request_captures",
    )
)

# Headers to forward from claude → Anthropic
FORWARD_HEADERS = [
    "authorization",
    "x-api-key",
    "anthropic-version",
    "anthropic-beta",
    "content-type",
]

# Hop-by-hop headers to skip in responses
SKIP_RESPONSE_HEADERS = {
    "content-encoding",
    "transfer-encoding",
    "connection",
    "keep-alive",
}


# -- Proxy server ------------------------------------------------------------


def _find_free_port() -> int:
    """Find an available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class _Proxy:
    """Localhost HTTP proxy between claude and Anthropic.

    Private to Engine — do not instantiate directly.

    Lifecycle:
        proxy = _Proxy()
        port = await proxy.start()
        # set ANTHROPIC_BASE_URL=http://127.0.0.1:{port} in subprocess env
        ...
        await proxy.stop()
    """

    DEFAULT_CONTEXT_WINDOW = CONTEXT_WINDOW

    def __init__(
        self,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
        upstream_url: str | None = None,
    ):
        self._context_window = context_window
        self._upstream_url = upstream_url or ANTHROPIC_API_URL

        self._port: int | None = None
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._http_client: httpx.AsyncClient | None = None

        # Per-request state — updated from SSE stream
        self._token_count = 0          # Total input tokens (sum for context window)
        self._input_tokens = 0         # Raw input tokens (non-cached)
        self._cache_creation_tokens = 0  # Tokens written to cache this request
        self._cache_read_tokens = 0    # Tokens read from cache this request
        self._output_tokens = 0        # Output tokens generated
        self._stop_reason: str | None = None  # e.g. "end_turn", "max_tokens"
        self._response_model: str | None = None  # Model from response
        self._response_id: str | None = None  # Message ID from response

        self._warned_no_api_key = False

        # Last API error — set on 4xx/5xx, cleared by consumer
        self._last_api_error: dict | None = None

        # Usage quota state (from Anthropic response headers)
        self._usage_7d: float | None = None
        self._usage_5h: float | None = None

        # Trace context — set by consumer before each turn so proxy spans
        # nest under the same trace as the turn span.
        self._trace_context: dict | None = None

    # -- Properties -----------------------------------------------------------

    @property
    def base_url(self) -> str:
        if self._port is None:
            raise RuntimeError("Proxy not started")
        return f"http://127.0.0.1:{self._port}"

    @property
    def port(self) -> int | None:
        return self._port

    @property
    def token_count(self) -> int:
        return self._token_count

    @property
    def context_window(self) -> int:
        return self._context_window

    @property
    def usage_7d(self) -> float | None:
        return self._usage_7d

    @property
    def usage_5h(self) -> float | None:
        return self._usage_5h

    @property
    def input_tokens(self) -> int:
        return self._input_tokens

    @property
    def total_input_tokens(self) -> int:
        """OTel-compliant total input tokens.

        Anthropic's raw input_tokens excludes cached tokens.  The OpenTelemetry
        semantic conventions for Anthropic (footnote [11]) require:
            gen_ai.usage.input_tokens = input_tokens
                + cache_read_input_tokens + cache_creation_input_tokens
        """
        return self._input_tokens + self._cache_creation_tokens + self._cache_read_tokens

    @property
    def cache_creation_tokens(self) -> int:
        return self._cache_creation_tokens

    @property
    def cache_read_tokens(self) -> int:
        return self._cache_read_tokens

    def pop_api_error(self) -> dict | None:
        """Return and clear the last API error, if any."""
        err = self._last_api_error
        self._last_api_error = None
        return err

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
        """Reset per-request state. Call after compaction."""
        self._token_count = 0
        self._input_tokens = 0
        self._cache_creation_tokens = 0
        self._cache_read_tokens = 0
        self._output_tokens = 0
        self._stop_reason = None
        self._response_model = None
        self._response_id = None

    def reset_output_tokens(self) -> None:
        """Reset just the output token accumulator. Call at turn start.

        The proxy accumulates output tokens with += across all API calls.
        Only the consumer (streaming.py) knows when a new turn begins,
        so it calls this before streaming starts.
        """
        self._output_tokens = 0

    def set_trace_context(self, ctx: dict | None) -> None:
        """Set trace context for proxy request handlers to inherit.

        Call with logfire.get_context() before each turn so proxy spans
        (like quota header logging) nest under the consumer's turn span.
        """
        self._trace_context = ctx

    # -- Lifecycle ------------------------------------------------------------

    async def start(self) -> int:
        """Start the proxy server. Returns the port number."""
        self._port = _find_free_port()
        self._http_client = httpx.AsyncClient(timeout=300.0)

        # 0 = no body size limit (API requests carry full conversation history)
        self._app = web.Application(client_max_size=0)
        self._app.router.add_route("*", "/{path:.*}", self._handle_request)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", self._port)
        await self._site.start()

        return self._port

    async def stop(self) -> None:
        """Stop the proxy server and release resources."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        if self._runner:
            await self._runner.cleanup()
            self._runner = None

        self._site = None
        self._app = None

    # -- Request handling -----------------------------------------------------

    async def _handle_request(self, request: web.Request) -> web.StreamResponse:
        """Route incoming requests.

        Attaches the consumer's trace context (if set) so any logfire
        calls within the handler nest under the same trace as the turn span.
        """
        ctx = (
            logfire.attach_context(self._trace_context)
            if self._trace_context
            else contextlib.nullcontext()
        )
        with ctx:
            path = "/" + request.match_info.get("path", "")

            if request.method == "GET" and path == "/health":
                return web.Response(text="ok")

            if request.method != "POST":
                return web.Response(status=404, text="Not found")

            try:
                return await self._forward_request(request, path)
            except Exception as e:
                return web.Response(status=500, text=str(e))

    async def _forward_request(
        self, request: web.Request, path: str
    ) -> web.StreamResponse:
        """Forward request to Anthropic, rewriting compact prompts."""
        body_bytes = await request.read()

        try:
            body = json.loads(body_bytes)
        except Exception:
            body = None

        # Debug capture
        if CAPTURE_REQUESTS and body is not None:
            self._capture_request(
                path, body, headers=dict(request.headers)
            )

        # Build forwarding headers
        headers = {}
        for header_name in FORWARD_HEADERS:
            value = request.headers.get(header_name)
            if value:
                headers[header_name] = value
        if "content-type" not in headers:
            headers["content-type"] = "application/json"

        # Forward to upstream (Anthropic, or a test fixture if configured)
        url = f"{self._upstream_url}{path}"

        # Trace every API call — shows what Claude Code does before first token.
        model = body.get("model", "?") if body else "?"
        max_tokens = body.get("max_tokens", "?") if body else "?"
        msg_count = len(body.get("messages", [])) if body else 0
        logfire.trace(
            "proxy.forward: {method} {path} model={model} msgs={msgs} max_tokens={max_tokens}",
            method=request.method,
            path=path,
            model=model,
            msgs=msg_count,
            max_tokens=max_tokens,
        )

        if self._http_client is None:
            raise RuntimeError("HTTP client not initialized")

        async with self._http_client.stream(
            "POST", url, content=body_bytes, headers=headers
        ) as response:
            # Sniff usage headers
            self._sniff_usage_headers(response.headers)

            # Error responses: record for exception system, then pass through
            if response.status_code >= 400:
                error_body = await response.aread()
                # Store for consumer (streaming.py) to emit as exception
                try:
                    error_text = error_body.decode("utf-8", errors="replace")[:500]
                except Exception:
                    error_text = "(undecodable)"
                self._last_api_error = {
                    "status": response.status_code,
                    "body": error_text,
                }
                resp = web.Response(status=response.status_code, body=error_body)
                for key, value in response.headers.items():
                    if key.lower() not in SKIP_RESPONSE_HEADERS:
                        resp.headers[key] = value
                return resp

            # Success: stream back to claude
            resp = web.StreamResponse(status=response.status_code)
            for key, value in response.headers.items():
                if key.lower() not in SKIP_RESPONSE_HEADERS:
                    resp.headers[key] = value

            await resp.prepare(request)

            # Sniff SSE stream for usage data while forwarding.
            # Scans for message_start (input tokens, model, id) and
            # message_delta (output tokens, stop reason).
            sse_buffer = ""

            async for chunk in response.aiter_bytes():
                await resp.write(chunk)

                sse_buffer += chunk.decode("utf-8", errors="replace")

                # Process complete lines, keep partial tail
                while "\n" in sse_buffer:
                    line, sse_buffer = sse_buffer.split("\n", 1)
                    line = line.strip()
                    if line.startswith("data: "):
                        raw = line[6:]
                        # Only parse events we care about
                        if '"message_start"' in raw or '"message_delta"' in raw:
                            self._process_sse_data(raw)

            await resp.write_eof()
            return resp

    # -- SSE usage extraction -------------------------------------------------

    def _process_sse_data(self, data: str) -> None:
        """Process an SSE data payload for usage information.

        Handles two event types from the Anthropic streaming API:
        - message_start: input tokens (with cache breakdown), model, response ID
        - message_delta: output tokens, stop reason
        """
        try:
            payload = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            return

        event_type = payload.get("type", "")

        if event_type == "message_start":
            message = payload.get("message", {})
            usage = message.get("usage", {})

            # No reset here — the proxy accumulates output tokens blindly
            # across all API calls. Only streaming.py knows when a turn
            # begins (via ResultEvent), so it calls reset_output_tokens()
            # at the right time. The claude subprocess produces multiple
            # end_turn stop reasons within a single user-facing turn,
            # so stop_reason is NOT a reliable turn boundary signal.

            self._input_tokens = usage.get("input_tokens", 0)
            self._cache_creation_tokens = usage.get("cache_creation_input_tokens", 0)
            self._cache_read_tokens = usage.get("cache_read_input_tokens", 0)
            # Total for context window tracking (all three count toward the window)
            self._token_count = (
                self._input_tokens
                + self._cache_creation_tokens
                + self._cache_read_tokens
            )
            self._response_model = message.get("model")
            self._response_id = message.get("id")

            logfire.debug(
                "proxy: message_start input={input} cache_read={cache_read} "
                "cache_create={cache_create} total={total} model={model}",
                input=self._input_tokens,
                cache_read=self._cache_read_tokens,
                cache_create=self._cache_creation_tokens,
                total=self._token_count,
                model=self._response_model,
            )

        elif event_type == "message_delta":
            usage = payload.get("usage", {})
            delta = payload.get("delta", {})

            new_tokens = usage.get("output_tokens", 0)
            self._output_tokens += new_tokens
            self._stop_reason = delta.get("stop_reason")
            logfire.debug(
                "proxy: message_delta +{new_tokens} = {total} stop_reason={stop_reason}",
                new_tokens=new_tokens,
                total=self._output_tokens,
                stop_reason=self._stop_reason,
            )

    # -- Usage headers --------------------------------------------------------

    def _sniff_usage_headers(self, headers: httpx.Headers) -> None:
        """Extract usage quota from Anthropic response headers."""
        util_7d = headers.get("anthropic-ratelimit-unified-7d-utilization")
        util_5h = headers.get("anthropic-ratelimit-unified-5h-utilization")

        # No debug logging here — the raw headers have been inspected and
        # confirmed: Anthropic sends 1-2 decimal places of precision.
        # The float() conversion below is lossless.

        if util_7d is not None:
            try:
                self._usage_7d = float(util_7d)
            except ValueError:
                pass

        if util_5h is not None:
            try:
                self._usage_5h = float(util_5h)
            except ValueError:
                pass

    # -- Debug capture --------------------------------------------------------

    def _capture_request(
        self,
        path: str,
        body: dict,
        suffix: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        """Dump request to JSON file for debugging.

        Captures both the JSON body and HTTP headers (in a sidecar file)
        so we can inspect exactly what Claude Code sends to Anthropic.
        """
        try:
            CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path_safe = path.replace("/", "_").strip("_")
            suffix_part = f"_{suffix}" if suffix else ""
            base = f"{timestamp}_{path_safe}{suffix_part}"

            # Body
            with open(CAPTURE_DIR / f"{base}.json", "w") as f:
                json.dump(body, f, indent=2, default=str)

            # Headers sidecar
            if headers:
                redacted = {}
                for k, v in headers.items():
                    kl = k.lower()
                    if kl in ("authorization", "x-api-key"):
                        # Show prefix only — enough to identify token type
                        redacted[k] = v[:20] + "..." if len(v) > 20 else v
                    else:
                        redacted[k] = v
                with open(CAPTURE_DIR / f"{base}_headers.json", "w") as f:
                    json.dump(redacted, f, indent=2)
        except Exception:
            pass

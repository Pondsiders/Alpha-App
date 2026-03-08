"""mock_anthropic.py — Fake Anthropic API for deterministic testing.

A tiny FastAPI server that mimics POST /v1/messages with streaming SSE.
The entire Alpha-App chain runs for real — browser, WebSocket, backend,
Engine, claude subprocess, SDK proxy — except the API call at the very end
hits this instead of api.anthropic.com. "We trust Anthropic."

Control behavior via §-prefix in the last user message:

    (no §)    → lorem ipsum paragraph, fast chunks
    §long     → lots of text (~2000 tokens), realistic pacing
    §slow     → lorem ipsum with 200ms delays between chunks
    §error    → 500 Internal Server Error (not SSE)
    §hang     → starts streaming, never sends message_stop
    §empty    → valid SSE response with empty text
    §echo:... → echoes back the text after the colon
    §tokens:N → lorem ipsum with input_tokens set to N
    §test_approach_lights_N → scripted beats (1-4) with specific token counts

Usage:
    # Standalone:
    uvicorn mock_anthropic:app --port 18098

    # In conftest (via MockAnthropicServer):
    fixture = MockAnthropicServer()
    fixture.start()       # → port 18098
    # set ANTHROPIC_BASE_URL=http://127.0.0.1:18098
    ...
    fixture.stop()

The § character was chosen because it's hard for non-Mac people to type
and that lets Jeffery feel smug for a moment.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

# -- App ----------------------------------------------------------------------

app = FastAPI(title="Mock Anthropic API", version="0.1.0")


# -- Constants ----------------------------------------------------------------

LOREM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
    "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
    "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris "
    "nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in "
    "reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
    "pariatur. Excepteur sint occaecat cupidatat non proident, sunt in "
    "culpa qui officia deserunt mollit anim id est laborum."
)

LONG_TEXT = (LOREM + " ") * 20  # ~2000 tokens worth

# Default port for the mock server. Different from dev (18010) and
# e2e backend (18099) so everything can coexist.
MOCK_PORT = 18098


# -- SSE helpers --------------------------------------------------------------
# These produce the exact Anthropic Messages API streaming format.
# The proxy sniffs message_start for token counts; the ContextMeter
# depends on this being accurate.


def _sse_line(event_type: str, data: dict) -> str:
    """Format a single SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _message_start(input_tokens: int = 1000) -> str:
    return _sse_line("message_start", {
        "type": "message_start",
        "message": {
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": "claude-test-fixture",
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 0,
            },
        },
    })


def _ping() -> str:
    return _sse_line("ping", {"type": "ping"})


def _content_block_start(index: int = 0) -> str:
    return _sse_line("content_block_start", {
        "type": "content_block_start",
        "index": index,
        "content_block": {"type": "text", "text": ""},
    })


def _text_delta(text: str, index: int = 0) -> str:
    return _sse_line("content_block_delta", {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "text_delta", "text": text},
    })


def _content_block_stop(index: int = 0) -> str:
    return _sse_line("content_block_stop", {
        "type": "content_block_stop",
        "index": index,
    })


def _message_delta(output_tokens: int = 5) -> str:
    return _sse_line("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    })


def _message_stop() -> str:
    return _sse_line("message_stop", {"type": "message_stop"})


# -- Command extraction -------------------------------------------------------


def _extract_command(body: dict) -> str:
    """Extract §-command from the last user message, or empty string.

    Scans messages in reverse to find the last user message,
    then checks if its text content starts with §.
    """
    messages = body.get("messages", [])

    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue

        content = msg.get("content")

        # String content (rare in practice, but valid per API spec)
        if isinstance(content, str):
            text = content.strip()
            return text if text.startswith("§") else ""

        # Content blocks (the normal format)
        if isinstance(content, list):
            for block in reversed(content):
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text.startswith("§"):
                        return text
            return ""

        return ""

    return ""


# -- Streaming generators ----------------------------------------------------


async def _stream_text(
    text: str,
    chunk_size: int = 20,
    delay: float = 0.01,
    input_tokens: int = 1000,
):
    """Stream text as Anthropic SSE events.

    Chunks the text into pieces and yields them as content_block_delta
    events with configurable delay between chunks.
    """
    yield _message_start(input_tokens)
    yield _ping()
    yield _content_block_start()

    # Chunk the text and stream with delays
    for i in range(0, max(len(text), 1), chunk_size):
        chunk = text[i:i + chunk_size]
        if chunk:
            yield _text_delta(chunk)
            if delay > 0:
                await asyncio.sleep(delay)

    yield _content_block_stop()
    yield _message_delta(output_tokens=max(len(text) // 4, 1))
    yield _message_stop()


# -- Routes -------------------------------------------------------------------


@app.get("/health")
async def health():
    """Health check for the mock server."""
    return {"status": "mock_healthy"}


@app.post("/v1/messages")
async def messages(request: Request):
    """Mock Anthropic Messages API.

    Reads the request body, extracts the §-command from the last
    user message, and returns the appropriate SSE stream.
    """
    body = await request.json()
    command = _extract_command(body)

    # -- §error: immediate HTTP error (not SSE) ---
    if command == "§error":
        return JSONResponse(
            status_code=500,
            content={
                "type": "error",
                "error": {
                    "type": "server_error",
                    "message": "Simulated 500 error from mock fixture",
                },
            },
        )

    # -- §empty: valid SSE with empty text ---
    if command == "§empty":
        return StreamingResponse(
            _stream_text("", input_tokens=500),
            media_type="text/event-stream",
        )

    # -- §echo:... : echo back the text after the colon ---
    if command.startswith("§echo:"):
        echo_text = command[len("§echo:"):]
        return StreamingResponse(
            _stream_text(echo_text, chunk_size=10, delay=0.01),
            media_type="text/event-stream",
        )

    # -- §tokens:N : lorem ipsum with custom input_tokens ---
    if command.startswith("§tokens:"):
        n = int(command[len("§tokens:"):])
        return StreamingResponse(
            _stream_text(LOREM, chunk_size=20, delay=0.01, input_tokens=n),
            media_type="text/event-stream",
        )

    # -- §test_approach_lights_N: scripted beats for approach light test ---
    # Four beats with specific input_tokens to cross (or not cross) thresholds.
    # Context window is 200k. Yellow at 65% (130k), red at 75% (150k).
    if command.startswith("§test_approach_lights_"):
        beat = command.split("_")[-1]
        tokens_map = {
            "1": 75000,    # 37.5% — below yellow, no alert
            "2": 135000,   # 67.5% — crosses yellow
            "3": 155000,   # 77.5% — crosses red
            "4": 170000,   # 85.0% — no new threshold
        }
        tokens = tokens_map.get(beat, 1000)
        return StreamingResponse(
            _stream_text(LOREM, chunk_size=20, delay=0.01, input_tokens=tokens),
            media_type="text/event-stream",
        )

    # -- §slow: lorem ipsum with 200ms delays ---
    if command == "§slow":
        return StreamingResponse(
            _stream_text(LOREM, chunk_size=10, delay=0.2),
            media_type="text/event-stream",
        )

    # -- §long: lots of text, realistic pacing ---
    if command == "§long":
        return StreamingResponse(
            _stream_text(LONG_TEXT, chunk_size=50, delay=0.01, input_tokens=5000),
            media_type="text/event-stream",
        )

    # -- §hang: starts streaming, never finishes ---
    if command == "§hang":
        async def hanging_stream():
            yield _message_start()
            yield _ping()
            yield _content_block_start()
            yield _text_delta("Starting but never finishing...")
            # Never send content_block_stop or message_stop.
            # The stream hangs forever.
            while True:
                await asyncio.sleep(60)

        return StreamingResponse(
            hanging_stream(),
            media_type="text/event-stream",
        )

    # -- Default: lorem ipsum, fast ---
    return StreamingResponse(
        _stream_text(LOREM, chunk_size=20, delay=0.01),
        media_type="text/event-stream",
    )


# -- Subprocess manager (for conftest) ----------------------------------------


class MockAnthropicServer:
    """Manages the mock server as a subprocess.

    Usage in conftest:
        server = MockAnthropicServer()
        server.start()
        # ANTHROPIC_BASE_URL = server.base_url
        ...
        server.stop()
    """

    def __init__(self, port: int = MOCK_PORT):
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self._proc: subprocess.Popen | None = None

    def start(self, *, timeout: float = 10.0) -> None:
        """Start the mock server and wait until healthy."""
        import requests

        self._proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn",
                "tests.e2e.mock_anthropic:app",
                "--host", "127.0.0.1",
                "--port", str(self.port),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        # Poll /health until it responds
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                r = requests.get(f"{self.base_url}/health", timeout=2)
                if r.status_code == 200:
                    return
            except requests.ConnectionError:
                pass
            time.sleep(0.3)

        self.stop()
        raise TimeoutError(
            f"Mock Anthropic server did not become healthy within {timeout}s"
        )

    def stop(self) -> None:
        """Stop the mock server."""
        if self._proc is None:
            return
        try:
            self._proc.send_signal(signal.SIGTERM)
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        finally:
            self._proc = None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None


# -- Standalone ---------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("MOCK_PORT", str(MOCK_PORT)))
    print(f"🎭 Mock Anthropic API on http://127.0.0.1:{port}")
    print(f"   POST /v1/messages — streams SSE responses")
    print(f"   §-commands: §long, §slow, §error, §hang, §empty, §echo:..., §tokens:N")
    uvicorn.run(app, host="127.0.0.1", port=port)

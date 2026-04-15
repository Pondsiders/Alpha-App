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
    §test_approach_lights_N → scripted beats for approach light testing
    [Context: yellow/red] → cascade responses for approach light interjections

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

import logfire
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

# -- App ----------------------------------------------------------------------

logfire.configure(service_name="mock-anthropic")
app = FastAPI(title="Mock Anthropic API", version="0.1.0")
logfire.instrument_fastapi(app)


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

# State for §error_once — tracks which conversations have already failed.
# Keyed by message count so each new §error_once message in the same
# conversation gets its own first-fail/second-succeed cycle.
_error_once_seen: set[str] = set()


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
    then scans ALL text blocks (any order) for a §-prefixed command.
    Enrichment (timestamps, memories) adds extra text blocks around
    the user's input, so we can't assume position — just find the §.
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

        # Content blocks — scan in REVERSE (most recent block first).
        # After enrichment, the content array has interleaved timestamp
        # and user-text blocks. Earlier §-commands from previous messages
        # in the same conversation can appear first if we scan forward.
        if isinstance(content, list):
            for block in reversed(content):
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text.startswith("§"):
                        return text
            return ""

        return ""

    return ""


def _last_user_text(body: dict) -> str:
    """Return the text of the last user message, or empty string.

    Like _extract_command but returns raw text regardless of § prefix.
    Used to detect approach light interjection messages in the conversation.
    """
    messages = body.get("messages", [])

    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue

        content = msg.get("content")

        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            for block in reversed(content):
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "").strip()
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
    msg_count = len(body.get("messages", []))
    last_role = body["messages"][-1]["role"] if body.get("messages") else "?"
    model = body.get("model", "?")

    command = _extract_command(body)

    # Log every request with full context
    logfire.info(
        "mock.request: {msg_count} msgs, last={last_role}, cmd={command}, model={model}",
        msg_count=msg_count,
        last_role=last_role,
        command=command or "(default)",
        model=model,
    )

    # Log the full conversation structure
    for i, msg in enumerate(body.get("messages", [])):
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, str):
            preview = content[:80]
            logfire.debug(
                "mock.msg[{i}] role={role} content={preview!r}",
                i=i, role=role, preview=preview,
            )
        elif isinstance(content, list):
            block_summary = [
                f"text:{b.get('text', '')[:60]!r}"
                if b.get("type") == "text"
                else f"{b.get('type', '?')}"
                for b in content
            ]
            logfire.debug(
                "mock.msg[{i}] role={role} blocks={blocks}",
                i=i, role=role, blocks=block_summary,
            )

    # -- §error: immediate HTTP error (not SSE) ---
    if command == "§error":
        logfire.warn("mock.respond: §error → 500 server_error")
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

    # -- §error_once: 500 on first attempt, success on retry ---
    # Tracks per-conversation (keyed by message count) so each NEW
    # §error_once message fails once and then succeeds.
    if command == "§error_once":
        msg_count = len(body.get("messages", []))
        key = f"error_once_{msg_count}"
        if key not in _error_once_seen:
            _error_once_seen.add(key)
            logfire.warn("mock.respond: §error_once → 500 (first attempt, key={key})", key=key)
            return JSONResponse(
                status_code=500,
                content={
                    "type": "error",
                    "error": {
                        "type": "server_error",
                        "message": "Simulated transient 500 (will succeed on retry)",
                    },
                },
            )
        # Retry hits here — succeed with lorem ipsum
        logfire.info("mock.respond: §error_once → 200 (retry succeeded, key={key})", key=key)
        return StreamingResponse(
            _stream_text(
                "I recovered from a transient 500 error. The retry worked!",
                chunk_size=10, delay=0.01,
            ),
            media_type="text/event-stream",
        )

    # -- §overloaded: Anthropic overloaded_error (529) ---
    # This is the specific error type Anthropic sends during high load.
    if command == "§overloaded":
        logfire.warn("mock.respond: §overloaded → 529 overloaded_error")
        return JSONResponse(
            status_code=529,
            content={
                "type": "error",
                "error": {
                    "type": "overloaded_error",
                    "message": "Simulated Anthropic overloaded error",
                },
            },
        )

    # -- §rate_limit: 429 Too Many Requests ---
    if command == "§rate_limit":
        logfire.warn("mock.respond: §rate_limit → 429 rate_limit_error")
        return JSONResponse(
            status_code=429,
            content={
                "type": "error",
                "error": {
                    "type": "rate_limit_error",
                    "message": "Simulated rate limit — Number of request tokens has exceeded your per-minute rate limit",
                },
            },
            headers={"retry-after": "5"},
        )

    # -- §empty: valid SSE with empty text ---
    if command == "§empty":
        logfire.info("mock.respond: §empty → 200 empty SSE")
        return StreamingResponse(
            _stream_text("", input_tokens=500),
            media_type="text/event-stream",
        )

    # -- §echo:... : echo back the text after the colon ---
    if command.startswith("§echo:"):
        echo_text = command[len("§echo:"):]
        logfire.info("mock.respond: §echo → 200 SSE echo={echo!r}", echo=echo_text[:60])
        return StreamingResponse(
            _stream_text(echo_text, chunk_size=10, delay=0.01),
            media_type="text/event-stream",
        )

    # -- §tokens:N : lorem ipsum with custom input_tokens ---
    if command.startswith("§tokens:"):
        n = int(command[len("§tokens:"):])
        logfire.info("mock.respond: §tokens:{n} → 200 SSE", n=n)
        return StreamingResponse(
            _stream_text(LOREM, chunk_size=20, delay=0.01, input_tokens=n),
            media_type="text/event-stream",
        )

    # -- §test_approach_lights_N: scripted beats for approach light test ---
    # Two beats: baseline (below yellow) and trigger (crosses yellow).
    # Context window is 200k. Yellow at 65% (130k), red at 75% (150k).
    # The cascade from yellow → red is handled by interjection detection below.
    if command.startswith("§test_approach_lights_"):
        beat = command.split("_")[-1]
        logfire.info("mock.respond: §test_approach_lights_{beat} → 200 SSE", beat=beat)
        tokens_map = {
            "1": 75000,    # 37.5% — below yellow, baseline
            "2": 135000,   # 67.5% — crosses yellow, triggers cascade
        }
        tokens = tokens_map.get(beat, 1000)
        return StreamingResponse(
            _stream_text(LOREM, chunk_size=20, delay=0.01, input_tokens=tokens),
            media_type="text/event-stream",
        )

    # -- Approach light interjection responses (cascade) ---
    # When an approach light fires, it sends a warning to claude's stdin.
    # Claude responds by calling the API with the warning in the conversation.
    # Match on the warning text and return the next stage of the cascade:
    #   yellow interjection → 77.5% (crosses red) → red fires
    #   red interjection    → 85%   (no new threshold) → cascade done
    if not command:
        last_text = _last_user_text(body)
        if "[Context: yellow]" in last_text:
            logfire.info("mock.respond: approach light yellow cascade → 155k tokens")
            return StreamingResponse(
                _stream_text(
                    "Acknowledged.", chunk_size=20, delay=0.01,
                    input_tokens=155000,  # 77.5% — crosses red
                ),
                media_type="text/event-stream",
            )
        if "[Context: red]" in last_text:
            logfire.info("mock.respond: approach light red cascade → 170k tokens")
            return StreamingResponse(
                _stream_text(
                    "Acknowledged.", chunk_size=20, delay=0.01,
                    input_tokens=170000,  # 85% — cascade terminus
                ),
                media_type="text/event-stream",
            )

    # -- §slow: lorem ipsum with 200ms delays ---
    if command == "§slow":
        logfire.info("mock.respond: §slow → 200 SSE (200ms delay)")
        return StreamingResponse(
            _stream_text(LOREM, chunk_size=10, delay=0.2),
            media_type="text/event-stream",
        )

    # -- §long: lots of text, realistic pacing ---
    if command == "§long":
        logfire.info("mock.respond: §long → 200 SSE (~2000 tokens)")
        return StreamingResponse(
            _stream_text(LONG_TEXT, chunk_size=50, delay=0.01, input_tokens=5000),
            media_type="text/event-stream",
        )

    # -- §hang: starts streaming, never finishes ---
    if command == "§hang":
        logfire.warn("mock.respond: §hang → 200 SSE (will never finish)")
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

    # -- §help: stream back the command reference ---
    if command == "§help":
        logfire.info("mock.respond: §help → 200 SSE help table")
        # Stream line-by-line so the Markdown table renders correctly.
        # Tables break if chunked mid-row.
        help_lines = [
            "# MockAnthropic Commands\n\n",
            "| Command | What it does |\n",
            "|---|---|\n",
            "| *(any message)* | Lorem ipsum paragraph, fast chunks (~10ms) |\n",
            "| `§help` | This help table |\n",
            "| `§long` | ~2000 tokens of lorem ipsum, realistic pacing |\n",
            "| `§slow` | Lorem ipsum with 200ms delays between chunks |\n",
            "| `§error` | Permanent HTTP 500 — every retry fails |\n",
            "| `§error_once` | 500 on first attempt, succeeds on retry |\n",
            "| `§overloaded` | HTTP 529 overloaded\\_error |\n",
            "| `§rate_limit` | HTTP 429 with retry-after header |\n",
            "| `§hang` | Starts streaming, never sends message\\_stop |\n",
            "| `§empty` | Valid SSE with empty text content |\n",
            "| `§echo:text` | Echoes back exactly what you type after the colon |\n",
            "| `§tokens:N` | Lorem ipsum with input\\_tokens set to N (for context meter) |\n",
            "| `§test_approach_lights_1` | 37.5% context usage (below yellow) |\n",
            "| `§test_approach_lights_2` | 67.5% context usage (crosses yellow threshold) |\n",
        ]

        help_text = "".join(help_lines)
        return StreamingResponse(
            _stream_text(help_text, chunk_size=len(help_text), delay=0),
            media_type="text/event-stream",
        )

    # -- §markdown: full Markdown demo ---
    if command == "§markdown":
        logfire.info("mock.respond: §markdown → 200 SSE markdown demo")
        md = """\
# The Corkscrew Problem

Duck anatomy is weirder than you think. The mallard drake has evolved one of the most baroque reproductive systems in the animal kingdom, and it tells us something unexpected about evolutionary arms races.

## Background

Most birds don't have a phallus at all — roughly 97% of bird species rely on a "cloacal kiss," a brief touch of reproductive openings. Ducks are among the 3% that went a different direction. A *very* different direction.

The mallard's phallus is:

- **Corkscrew-shaped** (counterclockwise spiral)
- Up to 17 inches long in some species
- Capable of full eversion in approximately 0.3 seconds

> "The first time I watched a duck phallus evert, I couldn't believe it. It was like a party favor from hell."
> — Dr. Patricia Brennan, evolutionary biologist

## The Arms Race

Female ducks evolved counter-measures. Their reproductive tract features:

1. **Clockwise spirals** — opposite to the male's counterclockwise
2. **Dead-end pouches** — false passages that lead nowhere
3. **Muscular control** — ability to contract and restrict access

This is a genuine evolutionary arms race, documented across multiple species:

| Species | Phallus Length | Female Counter-Adaptations |
|---------|---------------|---------------------------|
| Mallard | 8–10 cm | Moderate spiraling |
| Argentine Lake Duck | 42 cm (!!) | Extensive dead-end pouches |
| Muscovy Duck | 10–12 cm | Strong muscular control |
| Ruddy Duck | 20 cm | Multiple spiral turns |

### What This Tells Us

The correlation between male phallus elaboration and female tract complexity is `r = 0.95` across species — nearly perfect. More elaborate males → more elaborate female defenses.

```python
# Brennan et al. (2007) correlation analysis
import scipy.stats as stats

male_elaboration = [2.1, 4.8, 3.5, 5.2, 6.1, 7.3]
female_complexity = [1.8, 4.5, 3.2, 5.0, 5.8, 7.1]

r, p = stats.pearsonr(male_elaboration, female_complexity)
print(f"r = {r:.2f}, p = {p:.4f}")  # r = 0.99, p < 0.001
```

The math checks out: if $F(x) = \\alpha x^2 + \\beta$, where $x$ represents male elaboration, the quadratic fit explains 98% of variance in female complexity.

---

## The Broader Lesson

Sexual conflict drives morphological innovation faster than almost any other evolutionary pressure. The corkscrew isn't optimal for reproduction — it's optimal for *winning a contest*. The duck emoji 🦆 is concealing a weapon.

~~This is definitely not the weirdest thing in biology.~~ Actually, it might be.

- [x] Researched duck anatomy
- [x] Verified with peer-reviewed sources
- [ ] Recovered emotionally
"""
        return StreamingResponse(
            _stream_text(md, chunk_size=len(md), delay=0),
            media_type="text/event-stream",
        )

    # -- Default: lorem ipsum, fast ---
    logfire.info("mock.respond: default → 200 SSE lorem ipsum")
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
                "mock_anthropic:app",
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
    print(f"   §-commands: §long, §slow, §error, §error_once, §overloaded, §rate_limit, §hang, §empty, §echo:..., §tokens:N")
    uvicorn.run(app, host="127.0.0.1", port=port)

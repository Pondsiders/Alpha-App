#!/usr/bin/env python3
"""probe_compact.py — Capture JSON events during claude compaction.

Answers the question: what does claude emit on stdout when it compacts?

Two modes:
  auto    — Set CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=20, feed Gatsby chunks
            until auto-compact triggers organically.
  manual  — Feed two chunks to build context, then send /compact.

Both modes log every event with timestamps to stdout and a log file.
After the feed phase, sends one more message to capture post-compact flow.

Usage:
    python backend/scripts/probe_compact.py auto
    python backend/scripts/probe_compact.py manual

Gatsby text is fetched from Project Gutenberg and cached locally.
Uses Haiku to keep costs negligible.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path


# -- Config -------------------------------------------------------------------

GATSBY_URL = "https://www.gutenberg.org/cache/epub/64317/pg64317.txt"
GATSBY_CACHE = Path(__file__).parent / ".gatsby_cache.txt"
CHUNK_WORDS = 10_000  # ~13K tokens per chunk

MODEL = "claude-haiku-4-5-20251001"
AUTOCOMPACT_PCT = "20"  # trigger at 20% of context window

# How long to wait for between-turn events after a result (seconds)
INTER_TURN_TIMEOUT = 3.0


# -- Helpers ------------------------------------------------------------------


def _find_claude() -> str:
    """Find the claude binary."""
    which = shutil.which("claude")
    if which:
        return which
    try:
        import claude_agent_sdk._bundled as _bundled
        return str(Path(_bundled.__path__[0]) / "claude")
    except ImportError:
        pass
    print("ERROR: claude binary not found on PATH or in claude_agent_sdk")
    sys.exit(1)


def _fetch_gatsby() -> str:
    """Fetch The Great Gatsby from Project Gutenberg, cache locally."""
    if GATSBY_CACHE.exists():
        return GATSBY_CACHE.read_text()

    print("Fetching Gatsby from Project Gutenberg...")
    text = urllib.request.urlopen(GATSBY_URL).read().decode("utf-8")

    # Strip Gutenberg boilerplate
    start = text.find("In my younger and more vulnerable years")
    end = text.find("So we beat on, boats against the current")
    if start > 0 and end > start:
        # Include the full last paragraph
        end = text.find("\n\n", end)
        if end < 0:
            end = len(text)
        text = text[start:end]
    elif start > 0:
        text = text[start:]

    GATSBY_CACHE.write_text(text)
    print(f"Cached {len(text):,} chars to {GATSBY_CACHE}")
    return text


def _chunk_text(text: str, words_per_chunk: int) -> list[str]:
    """Split text into chunks of N words."""
    words = text.split()
    return [
        " ".join(words[i : i + words_per_chunk])
        for i in range(0, len(words), words_per_chunk)
    ]


# -- Protocol -----------------------------------------------------------------


def _make_init_request() -> dict:
    return {
        "type": "control_request",
        "request_id": "req_probe_init",
        "request": {"subtype": "initialize", "hooks": {}, "agents": {}},
    }


def _make_user_message(content: str, session_id: str = "") -> dict:
    return {
        "type": "user",
        "session_id": session_id,
        "message": {"role": "user", "content": content},
        "parent_tool_use_id": None,
    }


# -- Probe --------------------------------------------------------------------


class CompactProbe:
    """Wraps a claude subprocess and logs every JSON event."""

    def __init__(self, mode: str, log_path: Path):
        self.mode = mode
        self.log_path = log_path
        self.start_time = time.monotonic()
        self.event_count = 0
        self.session_id = ""
        self.log_file = None

    # -- Logging --------------------------------------------------------------

    def _ts(self) -> str:
        elapsed = time.monotonic() - self.start_time
        return f"{elapsed:8.3f}s"

    def log(self, tag: str, msg: str):
        line = f"[{self._ts()}] [{tag:>8}] {msg}"
        print(line)
        if self.log_file:
            self.log_file.write(line + "\n")
            self.log_file.flush()

    def log_event(self, raw: dict):
        """Log a JSON event. Always writes the full raw JSON to the log file."""
        self.event_count += 1
        n = self.event_count
        msg_type = raw.get("type", "unknown")

        if msg_type == "assistant":
            content = raw.get("message", {}).get("content", [])
            partial = " PARTIAL" if raw.get("is_partial") else ""
            blocks = []
            for block in content:
                btype = block.get("type", "?")
                if btype == "text":
                    blocks.append(f"text({len(block.get('text', ''))})")
                else:
                    blocks.append(btype)
            self.log("EVENT", f"#{n} {msg_type}{partial} [{', '.join(blocks)}]")

        elif msg_type == "result":
            sid = raw.get("session_id", "")
            cost = raw.get("total_cost_usd", 0)
            turns = raw.get("num_turns", 0)
            duration = raw.get("duration_ms", 0)
            self.session_id = sid
            self.log(
                "RESULT",
                f"#{n} session={sid[:20]}... cost=${cost:.4f} "
                f"turns={turns} duration={duration}ms",
            )

        elif msg_type == "system":
            subtype = raw.get("subtype", "")
            data = raw.get("data", "")
            preview = str(data)[:120] if data else ""
            self.log("SYSTEM", f"#{n} subtype={subtype} {preview}")

        else:
            # Anything we don't have a handler for — log it loud
            dump = json.dumps(raw)
            if len(dump) > 400:
                dump = dump[:400] + "..."
            self.log("EVENT", f"#{n} *** {msg_type} *** {dump}")

        # Always write full JSON to log file
        if self.log_file:
            self.log_file.write(f"    RAW: {json.dumps(raw)}\n")
            self.log_file.flush()

    # -- I/O ------------------------------------------------------------------

    async def _send(self, proc, msg: dict):
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        await proc.stdin.drain()

    async def _read_until_result(self, proc) -> dict | None:
        """Read events until a result event. Returns the result."""
        while True:
            line = await proc.stdout.readline()
            if not line:
                self.log("EOF", "stdout closed")
                return None
            text = line.decode().strip()
            if not text:
                continue
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                self.log("PARSE", f"Unparseable: {text[:200]}")
                continue

            self.log_event(raw)

            if raw.get("type") == "result":
                return raw

    async def _read_inter_turn(self, proc, timeout: float = INTER_TURN_TIMEOUT):
        """Read any events that arrive between turns (after result).

        Auto-compact might fire between turns. This catches those events.
        """
        try:
            while True:
                line = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=timeout
                )
                if not line:
                    return
                text = line.decode().strip()
                if not text:
                    continue
                raw = json.loads(text)
                self.log("BETWEEN", "--- inter-turn event ---")
                self.log_event(raw)
        except (asyncio.TimeoutError, TimeoutError):
            pass  # No more events within timeout — expected

    async def _drain_stderr(self, proc):
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            # stderr is noisy — only log to file, not stdout
            text = line.decode().strip()
            if text and self.log_file:
                self.log_file.write(f"    STDERR: {text}\n")

    # -- Main flow ------------------------------------------------------------

    async def run(self):
        self.log_file = open(self.log_path, "w")

        self.log("START", f"mode={self.mode} model={MODEL}")
        self.log("START", f"log → {self.log_path}")

        claude = _find_claude()
        self.log("INIT", f"binary: {claude}")

        # Fetch and chunk Gatsby
        gatsby = _fetch_gatsby()
        chunks = _chunk_text(gatsby, CHUNK_WORDS)
        self.log(
            "INIT",
            f"Gatsby: {len(gatsby):,} chars → {len(chunks)} chunks "
            f"of ~{CHUNK_WORDS:,} words",
        )

        # Build environment
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        if self.mode == "auto":
            env["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = AUTOCOMPACT_PCT
            self.log("INIT", f"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE={AUTOCOMPACT_PCT}")

        # Start claude
        cmd = [
            claude,
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            "--model", MODEL,
            "--permission-mode", "bypassPermissions",
            "--system-prompt",
            "You are a helpful assistant. Acknowledge text briefly.",
            "--include-partial-messages",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self.log("INIT", f"pid={proc.pid}")

        asyncio.create_task(self._drain_stderr(proc))

        # Init handshake
        await self._send(proc, _make_init_request())
        while True:
            line = await proc.stdout.readline()
            if not line:
                self.log("ERROR", "EOF during init")
                return
            text = line.decode().strip()
            if not text:
                continue
            raw = json.loads(text)
            self.log("INIT", f"type={raw.get('type')}")
            if raw.get("type") == "control_response":
                break

        self.log("INIT", "Handshake complete")
        self.log("─" * 4, "═" * 60)

        # Phase 1: Feed chunks
        if self.mode == "auto":
            await self._run_auto(proc, chunks)
        else:
            await self._run_manual(proc, chunks)

        # Phase 2: Post-compact message
        self.log("─" * 4, "═" * 60)
        self.log("POST", "Sending post-compact message...")
        await self._send(
            proc,
            _make_user_message(
                "What were we just talking about? Summarize briefly."
            ),
        )
        await self._read_until_result(proc)
        self.log("POST", "Post-compact turn complete")

        # Cleanup
        proc.stdin.close()
        await proc.wait()

        self.log("─" * 4, "═" * 60)
        self.log("DONE", f"Total events logged: {self.event_count}")
        self.log("DONE", f"Full log: {self.log_path}")
        self.log_file.close()

    async def _run_auto(self, proc, chunks: list[str]):
        """Feed Gatsby chunks until we've sent them all.

        Auto-compact should trigger organically. We log everything
        and let the data speak — no programmatic detection needed.
        """
        for i, chunk in enumerate(chunks):
            word_count = len(chunk.split())
            self.log(
                "FEED",
                f"Chunk {i + 1}/{len(chunks)} ({word_count:,} words)...",
            )

            await self._send(
                proc,
                _make_user_message(
                    f"Please read and briefly acknowledge this passage "
                    f"(chunk {i + 1} of {len(chunks)}):\n\n{chunk}"
                ),
            )

            result = await self._read_until_result(proc)
            if result is None:
                self.log("ERROR", "Lost connection")
                return

            # Check for events between turns — auto-compact might fire here
            self.log("FEED", f"Chunk {i + 1} done. Checking for inter-turn events...")
            await self._read_inter_turn(proc)

    async def _run_manual(self, proc, chunks: list[str]):
        """Feed two chunks, then send /compact."""
        for i in range(min(2, len(chunks))):
            word_count = len(chunks[i].split())
            self.log("FEED", f"Chunk {i + 1}/2 ({word_count:,} words)...")

            await self._send(
                proc,
                _make_user_message(
                    f"Please read and briefly acknowledge this passage "
                    f"(chunk {i + 1}):\n\n{chunks[i]}"
                ),
            )

            result = await self._read_until_result(proc)
            if result is None:
                self.log("ERROR", "Lost connection")
                return

            self.log("FEED", f"Chunk {i + 1} acknowledged")

        # Send /compact
        self.log("─" * 4, "═" * 60)
        self.log("COMPACT", "Sending /compact...")
        await self._send(proc, _make_user_message("/compact"))

        # Read everything — this is THE DATA
        result = await self._read_until_result(proc)
        if result is None:
            self.log("ERROR", "Lost connection during /compact")
            return

        self.log("COMPACT", "Manual compact turn complete")

        # Check for any trailing events
        await self._read_inter_turn(proc)


# -- Entry point --------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Probe claude's compaction behavior — capture every JSON event"
    )
    parser.add_argument(
        "mode",
        choices=["auto", "manual"],
        help="auto = feed until autocompact, manual = /compact after 2 chunks",
    )
    args = parser.parse_args()

    # Log file with timestamp
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"probe_{args.mode}_{timestamp}.log"

    probe = CompactProbe(mode=args.mode, log_path=log_path)
    asyncio.run(probe.run())


if __name__ == "__main__":
    main()

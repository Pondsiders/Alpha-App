#!/usr/bin/env python3
"""probe_handoff_queue.py — Test the handoff double-queue theory.

The theory: drop /compact AND a follow-up message onto stdin back-to-back.
The /compact fires first, generates the summary, buffers it. The follow-up
message fires next as the "next user message" — summary piggybacks onto it.

If this works, handoff is two writes and zero machinery.

Usage:
    python backend/scripts/probe_handoff_queue.py

Logs every event with timestamps. Uses Haiku to keep costs negligible.
"""

from __future__ import annotations

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

# The follow-up message that should fire after compaction.
# In production this would be orientation + welcome back.
FOLLOWUP_MESSAGE = (
    "You just went through a compaction. This is a test. "
    "Please confirm you can see this message and briefly describe "
    "what you remember from before compaction."
)


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

    start = text.find("In my younger and more vulnerable years")
    end = text.find("So we beat on, boats against the current")
    if start > 0 and end > start:
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


class HandoffQueueProbe:
    """Test: /compact + follow-up queued back-to-back on stdin."""

    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.start_time = time.monotonic()
        self.event_count = 0
        self.session_id = ""
        self.log_file = None

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
                    text = block.get("text", "")
                    blocks.append(f"text({len(text)}ch)")
                else:
                    blocks.append(btype)
            self.log("EVENT", f"#{n} {msg_type}{partial} [{', '.join(blocks)}]")

        elif msg_type == "result":
            sid = raw.get("session_id", "")
            cost = raw.get("total_cost_usd", 0)
            turns = raw.get("num_turns", 0)
            self.session_id = sid
            self.log("RESULT", f"#{n} session={sid[:20]}... cost=${cost:.4f} turns={turns}")

        elif msg_type == "system":
            subtype = raw.get("subtype", "")
            extra = ""
            if subtype == "compact_boundary":
                meta = raw.get("compact_metadata", {})
                extra = f" trigger={meta.get('trigger')} pre_tokens={meta.get('pre_tokens')}"
            elif subtype == "status":
                extra = f" status={raw.get('status')}"
            self.log("SYSTEM", f"#{n} subtype={subtype}{extra}")

        else:
            dump = json.dumps(raw)
            if len(dump) > 400:
                dump = dump[:400] + "..."
            self.log("EVENT", f"#{n} *** {msg_type} *** {dump}")

        if self.log_file:
            self.log_file.write(f"    RAW: {json.dumps(raw)}\n")
            self.log_file.flush()

    async def _send(self, proc, msg: dict):
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        await proc.stdin.drain()

    async def _read_all_events(self, proc, label: str = ""):
        """Read ALL events until stdout closes or we hit a long silence.

        No early termination on result — we want to see EVERYTHING.
        """
        while True:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=30.0)
            except (asyncio.TimeoutError, TimeoutError):
                self.log("TIMEOUT", f"30s silence{' (' + label + ')' if label else ''}")
                return

            if not line:
                self.log("EOF", "stdout closed")
                return

            text = line.decode().strip()
            if not text:
                continue

            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                self.log("PARSE", f"Unparseable: {text[:200]}")
                continue

            self.log_event(raw)

    async def _drain_stderr(self, proc):
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text = line.decode().strip()
            if text and self.log_file:
                self.log_file.write(f"    STDERR: {text}\n")

    async def run(self):
        self.log_file = open(self.log_path, "w")

        self.log("START", f"Handoff queue probe — model={MODEL}")
        self.log("START", f"log → {self.log_path}")

        claude = _find_claude()
        self.log("INIT", f"binary: {claude}")

        # Fetch and chunk Gatsby
        gatsby = _fetch_gatsby()
        chunks = _chunk_text(gatsby, CHUNK_WORDS)
        self.log("INIT", f"Gatsby: {len(gatsby):,} chars → {len(chunks)} chunks")

        # Build environment — no auto-compact override, we're doing manual
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

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
        self.log("────", "═" * 60)

        # Phase 1: Feed 2 chunks to build context
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

            # Read until result for this chunk
            while True:
                line = await proc.stdout.readline()
                if not line:
                    self.log("ERROR", "EOF during chunk feed")
                    return
                text = line.decode().strip()
                if not text:
                    continue
                raw = json.loads(text)
                self.log_event(raw)
                if raw.get("type") == "result":
                    break

            self.log("FEED", f"Chunk {i + 1} acknowledged")

        # Phase 2: THE TEST — queue /compact and follow-up back-to-back
        self.log("────", "═" * 60)
        self.log("TEST", ">>> Queuing /compact and follow-up BACK TO BACK <<<")
        self.log("TEST", "Sending /compact...")
        await self._send(proc, _make_user_message("/compact"))

        self.log("TEST", "Immediately sending follow-up message...")
        await self._send(proc, _make_user_message(FOLLOWUP_MESSAGE))

        self.log("TEST", "Both messages queued. Now watching stdout...")
        self.log("────", "═" * 60)

        # Phase 3: Read EVERYTHING — don't stop at first result
        await self._read_all_events(proc, "post-queue")

        # Cleanup
        try:
            proc.stdin.close()
        except Exception:
            pass
        await proc.wait()

        self.log("────", "═" * 60)
        self.log("DONE", f"Total events logged: {self.event_count}")
        self.log("DONE", f"Full log: {self.log_path}")
        self.log_file.close()


# -- Entry point --------------------------------------------------------------


def main():
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"probe_handoff_queue_{timestamp}.log"

    probe = HandoffQueueProbe(log_path=log_path)
    asyncio.run(probe.run())


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""probe_streaming.py — Measure streaming timing via the Claude Agent SDK.

This is what production Alpha-App uses. `ClaudeSDKClient.receive_messages()`
is the iterator we actually drain from. Whatever timing behavior shows up
here is what the frontend will see.

Answers the question: how smoothly do text_delta and input_json_delta
arrive from the SDK's message iterator under the current stack
(Claude Agent SDK 0.1.61 / Claude Code 2.1.112 / Opus 4.7)?

Two modes:
  tool  — Ask Claude to Write ~5000 tokens to /tmp/streaming_test.md.
  text  — Ask Claude for a 500-word story, no tools.

Usage:
    python backend/scripts/probe_streaming.py tool
    python backend/scripts/probe_streaming.py text
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import IO

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    UserMessage,
)


MODEL = "claude-opus-4-7"

TOOL_PROMPT = (
    "Please use the Write tool to create a file at /tmp/streaming_test.md "
    "containing approximately 5000 tokens of content on a topic of your "
    "choice. Do it in a single Write tool call — include the entire "
    "content as the `content` argument. Don't split it across multiple "
    "tool calls."
)

TEXT_PROMPT = (
    "Write me a 500-word story about a raccoon who discovers a vending "
    "machine in the woods. Do not use any tools — respond directly with "
    "the story as your message."
)


class StreamingProbe:
    def __init__(self, mode: str, log_path: Path):
        self.mode = mode
        self.log_path = log_path
        self.query_sent_time: float | None = None
        self.event_count = 0
        self.log_file: IO[str] | None = None
        self.text_deltas: list[tuple[int, int]] = []
        self.json_deltas: list[tuple[int, int]] = []
        self.thinking_deltas: list[tuple[int, int]] = []

    def _ms(self) -> int:
        if self.query_sent_time is None:
            return 0
        return int((time.monotonic() - self.query_sent_time) * 1000)

    def log(self, tag: str, msg: str):
        line = f"[{self._ms():>7}ms] [{tag:>12}] {msg}"
        print(line)
        if self.log_file:
            self.log_file.write(line + "\n")
            self.log_file.flush()

    def handle_stream_event(self, event: dict):
        t_ms = self._ms()
        etype = event.get("type", "?")

        if etype == "content_block_start":
            block = event.get("content_block", {})
            btype = block.get("type", "?")
            if btype == "tool_use":
                name = block.get("name", "?")
                self.log("BLK_START", f"idx={event.get('index')} tool_use name={name}")
            else:
                self.log("BLK_START", f"idx={event.get('index')} {btype}")
        elif etype == "content_block_delta":
            delta = event.get("delta", {})
            dtype = delta.get("type", "?")
            if dtype == "text_delta":
                text = delta.get("text", "")
                self.text_deltas.append((t_ms, len(text)))
                preview = text[:40].replace("\n", "\\n")
                self.log("text_delta", f"+{len(text):>4}ch  {preview!r}")
            elif dtype == "input_json_delta":
                partial = delta.get("partial_json", "")
                self.json_deltas.append((t_ms, len(partial)))
                preview = partial[:40].replace("\n", "\\n")
                self.log("json_delta", f"+{len(partial):>4}ch  {preview!r}")
            elif dtype == "thinking_delta":
                thinking = delta.get("thinking", "")
                self.thinking_deltas.append((t_ms, len(thinking)))
                preview = thinking[:40].replace("\n", "\\n")
                self.log("think_delta", f"+{len(thinking):>4}ch  {preview!r}")
            else:
                self.log("delta", f"type={dtype} {json.dumps(delta)[:100]}")
        elif etype == "content_block_stop":
            self.log("BLK_STOP", f"idx={event.get('index')}")
        elif etype == "message_start":
            self.log("MSG_START", "")
        elif etype == "message_delta":
            delta = event.get("delta", {})
            usage = event.get("usage", {})
            self.log("MSG_DELTA", f"stop={delta.get('stop_reason')} usage={json.dumps(usage)[:80]}")
        elif etype == "message_stop":
            self.log("MSG_STOP", "")
        else:
            self.log("stream", f"{etype} {json.dumps(event)[:120]}")

    def log_message(self, msg):
        self.event_count += 1
        if isinstance(msg, StreamEvent):
            self.handle_stream_event(msg.event)
        elif isinstance(msg, AssistantMessage):
            blocks = []
            for b in msg.content:
                bt = type(b).__name__
                if bt == "TextBlock":
                    blocks.append(f"text({len(b.text)})")
                elif bt == "ToolUseBlock":
                    blocks.append(f"tool_use({b.name})")
                elif bt == "ThinkingBlock":
                    blocks.append(f"thinking({len(b.thinking)})")
                else:
                    blocks.append(bt)
            self.log("Assistant", f"[{', '.join(blocks)}]")
        elif isinstance(msg, UserMessage):
            self.log("User-echo", "(tool result)")
        elif isinstance(msg, SystemMessage):
            self.log("System", f"subtype={msg.subtype}")
        elif isinstance(msg, ResultMessage):
            self.log(
                "Result",
                f"cost=${msg.total_cost_usd or 0:.4f} "
                f"duration={msg.duration_ms}ms turns={msg.num_turns}",
            )
        else:
            self.log(type(msg).__name__, "")

    def print_summary(self):
        line = "=" * 72
        print()
        print(line)
        print(f"SUMMARY — mode={self.mode}, {self.event_count} total messages")
        print(line)
        for name, deltas in (
            ("text_delta", self.text_deltas),
            ("input_json_delta", self.json_deltas),
            ("thinking_delta", self.thinking_deltas),
        ):
            if not deltas:
                print(f"  {name:20s}: (none)")
                continue
            t_first = deltas[0][0]
            t_last = deltas[-1][0]
            span = t_last - t_first
            n = len(deltas)
            total_chars = sum(d[1] for d in deltas)
            avg_gap = (span / (n - 1)) if n > 1 else 0
            print(
                f"  {name:20s}: {n:>4} events, "
                f"{total_chars:>6} chars, "
                f"first={t_first:>5}ms, "
                f"last={t_last:>5}ms, "
                f"span={span:>5}ms, "
                f"avg gap={avg_gap:>6.1f}ms"
            )
        print(line)

    async def run(self):
        self.log_file = open(self.log_path, "w")
        print(f"[START] mode={self.mode} model={MODEL} via ClaudeSDKClient")
        print(f"[START] log → {self.log_path}")

        options = ClaudeAgentOptions(
            model=MODEL,
            include_partial_messages=True,
            permission_mode="bypassPermissions",
            system_prompt="You are a helpful assistant.",
        )

        async with ClaudeSDKClient(options=options) as client:
            prompt = TOOL_PROMPT if self.mode == "tool" else TEXT_PROMPT
            self.query_sent_time = time.monotonic()
            print(f"[      0ms] [     QUERY  ] {prompt[:80]}...")
            await client.query(prompt)

            async for msg in client.receive_messages():
                self.log_message(msg)
                if isinstance(msg, ResultMessage):
                    break

        self.print_summary()
        if self.log_file:
            self.log_file.close()


def main():
    parser = argparse.ArgumentParser(
        description="Probe Claude streaming timing via the Agent SDK"
    )
    parser.add_argument(
        "mode",
        choices=["tool", "text"],
        help="tool = trigger a Write tool call, text = prose response only",
    )
    args = parser.parse_args()

    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"streaming_sdk_{args.mode}_{timestamp}.log"

    probe = StreamingProbe(mode=args.mode, log_path=log_path)
    asyncio.run(probe.run())


if __name__ == "__main__":
    main()

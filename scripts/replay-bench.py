#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "websockets>=14.0",
# ]
# ///
"""Benchmark chat replay over WebSocket — browser-free timing.

Usage:
    ./scripts/replay-bench.py <chat-id>
    ./scripts/replay-bench.py OQBLJibHMcSX
    ./scripts/replay-bench.py OQBLJibHMcSX --host primer.tail8bd569.ts.net --port 18020
"""

import asyncio
import json
import ssl
import sys
import time
from collections import Counter

import websockets


async def bench(chat_id: str, host: str, port: int) -> None:
    url = f"wss://{host}:{port}/ws"
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    async with websockets.connect(url, ssl=ssl_ctx) as ws:
        # Send replay request
        await ws.send(json.dumps({"type": "replay", "chatId": chat_id}))

        counts: Counter[str] = Counter()
        t0 = time.perf_counter()
        first_event_at: float | None = None

        async for raw in ws:
            event = json.loads(raw)
            event_type = event.get("type", "unknown")
            counts[event_type] += 1

            if first_event_at is None:
                first_event_at = time.perf_counter()

            if event_type == "replay-done":
                break

        elapsed = time.perf_counter() - t0
        total = sum(counts.values())

        print(f"\nReplay {chat_id}")
        print(f"  {total} events in {elapsed:.3f}s ({total / elapsed:.0f} events/sec)")
        print(f"  Breakdown:")
        for event_type, count in counts.most_common():
            print(f"    {event_type}: {count}")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        sys.exit(0)

    chat_id = sys.argv[1]
    host = "primer.tail8bd569.ts.net"
    port = 18020

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--host" and i + 1 < len(sys.argv):
            host = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
            i += 2
        else:
            print(f"Unknown argument: {sys.argv[i]}", file=sys.stderr)
            sys.exit(1)

    asyncio.run(bench(chat_id, host, port))


if __name__ == "__main__":
    main()

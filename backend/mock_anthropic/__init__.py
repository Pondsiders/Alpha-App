"""MockAnthropic — a minimal stand-in for the Anthropic Messages API.

Streams canned responses on POST /v1/messages so the Claude Agent SDK
can be exercised without making real API calls. Point a client at this
server with `ANTHROPIC_BASE_URL=http://host:port`.
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

CANNED_TEXT = "Hello, human."
CHUNK_SIZE = 60
CHUNK_DELAY_S = 0.25


def _new_message_id() -> str:
    return f"msg_mock_{uuid.uuid4().hex[:24]}"


def _chunks(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


async def _stream_response(model: str) -> AsyncIterator[bytes]:
    message_id = _new_message_id()
    text = CANNED_TEXT
    chunks = _chunks(text, CHUNK_SIZE)
    input_tokens = 10
    output_tokens = max(1, len(text) // 4)

    yield _sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        },
    )

    yield _sse(
        "content_block_start",
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
    )

    for chunk in chunks:
        await asyncio.sleep(CHUNK_DELAY_S)
        yield _sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": chunk},
            },
        )
        yield _sse("ping", {"type": "ping"})

    yield _sse(
        "content_block_stop",
        {"type": "content_block_stop", "index": 0},
    )

    yield _sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        },
    )

    yield _sse("message_stop", {"type": "message_stop"})


def _full_response(model: str) -> dict[str, Any]:
    text = CANNED_TEXT
    return {
        "id": _new_message_id(),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 10,
            "output_tokens": max(1, len(text) // 4),
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


def create_app() -> FastAPI:
    """Build the FastAPI app for MockAnthropic."""
    app = FastAPI(title="MockAnthropic", version="0.1.0")

    @app.post("/v1/messages")
    async def messages(request: Request) -> Any:
        body = await request.json()
        model: str = body.get("model", "mock-claude")
        stream: bool = body.get("stream", False)

        if stream:
            return StreamingResponse(
                _stream_response(model),
                media_type="text/event-stream",
            )
        return JSONResponse(_full_response(model))

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        return {
            "data": [
                {
                    "id": "mock-claude",
                    "type": "model",
                    "display_name": "Mock Claude",
                    "created_at": int(time.time()),
                }
            ],
            "has_more": False,
        }

    return app

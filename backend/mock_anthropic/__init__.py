"""MockAnthropic — a minimal stand-in for the Anthropic Messages API.

Echoes the last user message back on POST /v1/messages so the Claude
Agent SDK can be exercised without making real API calls. Point a client
at this server with `ANTHROPIC_BASE_URL=http://host:port`.

Implementation note: request and response shapes come from
`anthropic.types`. The official SDK already models every field on the
wire; we lean on those models instead of hand-rolling JSON. The request
side gets a tiny local Pydantic model that pulls out only what we need
(model, messages, stream); everything else is tolerated via
`extra="allow"`. The response side uses Anthropic's `Message` and the
`Raw*` streaming-event family directly, so MockAnthropic emits exactly
the shapes a real Anthropic-compatible client expects.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any, ClassVar, Literal

from anthropic.types import (
    Message,
    MessageDeltaUsage,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    TextBlock,
    TextDelta,
    Usage,
)
from anthropic.types.raw_message_delta_event import Delta as MessageDelta
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

DEFAULT_TEXT = "Hello, human."
CHUNK_SIZE = 60
CHUNK_DELAY_S = 0.25


class _IncomingMessage(BaseModel):
    """One message in the request body — only the fields MockAnthropic reads."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    role: Literal["user", "assistant"]
    content: str | list[dict[str, Any]]


class _IncomingRequest(BaseModel):
    """The Messages API request body — only the fields MockAnthropic reads.

    `extra="allow"` keeps the model permissive about unknown keys (tools,
    system, temperature, …); we don't care about them, but rejecting them
    would make MockAnthropic fail on real-shaped requests.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    model: str = "mock-claude"
    messages: list[_IncomingMessage]
    stream: bool = False


def _new_message_id() -> str:
    return f"msg_mock_{uuid.uuid4().hex[:24]}"


def _chunks(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def _sse(event_type: str, data: BaseModel) -> bytes:
    """Format a Pydantic event as one SSE frame."""
    payload = data.model_dump_json(exclude_none=True)
    return f"event: {event_type}\ndata: {payload}\n\n".encode()


def _extract_last_user_text(messages: list[_IncomingMessage]) -> str:
    """Pull plain text out of the last user-role message.

    Anthropic accepts `content` as either a bare string or a list of typed
    content blocks. We concatenate the text blocks of the last user-role
    message; anything else is ignored. Returns `DEFAULT_TEXT` if no user
    text is found.
    """
    for message in reversed(messages):
        if message.role != "user":
            continue
        if isinstance(message.content, str):
            return message.content or DEFAULT_TEXT
        parts = [
            block.get("text", "")
            for block in message.content
            if block.get("type") == "text" and isinstance(block.get("text"), str)
        ]
        joined = "".join(parts)
        if joined:
            return joined
    return DEFAULT_TEXT


def _usage(input_tokens: int, output_tokens: int) -> Usage:
    """Build a Usage block with our zeroed-out cache fields."""
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        server_tool_use=None,
        service_tier=None,
    )


async def _stream_response(model: str, text: str) -> AsyncIterator[bytes]:
    """Stream a complete Anthropic-format SSE response for `text`."""
    message_id = _new_message_id()
    chunks = _chunks(text, CHUNK_SIZE)
    input_tokens = 10
    output_tokens = max(1, len(text) // 4)

    yield _sse(
        "message_start",
        RawMessageStartEvent(
            type="message_start",
            message=Message(
                id=message_id,
                type="message",
                role="assistant",
                model=model,
                content=[],
                stop_reason=None,
                stop_sequence=None,
                usage=_usage(input_tokens, 0),
            ),
        ),
    )

    yield _sse(
        "content_block_start",
        RawContentBlockStartEvent(
            type="content_block_start",
            index=0,
            content_block=TextBlock(type="text", text="", citations=None),
        ),
    )

    for chunk in chunks:
        await asyncio.sleep(CHUNK_DELAY_S)
        yield _sse(
            "content_block_delta",
            RawContentBlockDeltaEvent(
                type="content_block_delta",
                index=0,
                delta=TextDelta(type="text_delta", text=chunk),
            ),
        )

    yield _sse(
        "content_block_stop",
        RawContentBlockStopEvent(type="content_block_stop", index=0),
    )

    yield _sse(
        "message_delta",
        RawMessageDeltaEvent(
            type="message_delta",
            delta=MessageDelta(stop_reason="end_turn", stop_sequence=None),
            usage=MessageDeltaUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
                server_tool_use=None,
            ),
        ),
    )

    yield _sse(
        "message_stop",
        RawMessageStopEvent(type="message_stop"),
    )


def _full_response(model: str, text: str) -> Message:
    """Build a non-streaming Message response for `text`."""
    return Message(
        id=_new_message_id(),
        type="message",
        role="assistant",
        model=model,
        content=[TextBlock(type="text", text=text, citations=None)],
        stop_reason="end_turn",
        stop_sequence=None,
        usage=_usage(10, max(1, len(text) // 4)),
    )


def create_app() -> FastAPI:
    """Build the FastAPI app for MockAnthropic."""
    app = FastAPI(title="MockAnthropic", version="0.1.0")

    @app.post("/v1/messages")
    async def messages(  # pyright: ignore[reportUnusedFunction]  # registered via decorator
        request: _IncomingRequest,
    ) -> Any:
        text = _extract_last_user_text(request.messages)

        if request.stream:
            return StreamingResponse(
                _stream_response(request.model, text),
                media_type="text/event-stream",
            )
        return JSONResponse(
            _full_response(request.model, text).model_dump(
                exclude_none=True, mode="json"
            )
        )

    return app

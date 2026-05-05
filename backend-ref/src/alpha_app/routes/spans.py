"""spans.py — Logfire span helpers for gen_ai semantic conventions.

Formats input/output messages for Logfire Model Run cards.
set_turn_span_response() sets all gen_ai attributes on the turn span
when a ResultEvent arrives.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alpha_app.chat import Chat
    from alpha_app.models import AssistantMessage


def build_prompt_preview(content: list[dict], max_len: int = 50) -> str:
    """Extract a short preview from content blocks for span naming."""
    for block in content:
        if block.get("type") == "text":
            text = block.get("text", "")
            if text:
                return text[:max_len] + ("\u2026" if len(text) > max_len else "")
    return "(no text)"


def format_input_messages(content: list[dict]) -> list[dict]:
    """Format content blocks as gen_ai.input.messages for Logfire Model Run card.

    Logfire expects: [{"role": "user", "parts": [{"type": "text", "content": "..."}]}]
    We receive Messages API blocks: [{"type": "text", "text": "..."}, ...]
    """
    parts = []
    for block in content:
        block_type = block.get("type", "")
        if block_type == "text":
            parts.append({"type": "text", "content": block.get("text", "")})
        elif block_type == "image":
            source = block.get("source", {})
            if source.get("type") == "base64":
                data = source.get("data", "")
                media_type = source.get("media_type", "image/jpeg")
                data_uri = f"data:{media_type};base64,{data}"
                parts.append({
                    "type": "uri",
                    "content": data_uri,
                    "media_type": media_type,
                })
            else:
                media = source.get("media_type", "image")
                parts.append({"type": "image", "content": f"({media})"})
        else:
            parts.append({"type": block_type, "content": f"({block_type})"})
    return [{"role": "user", "parts": parts}]


def format_output_messages(output_parts: list[dict]) -> list[dict]:
    """Format assistant content blocks as gen_ai.output.messages for Logfire."""
    parts = []
    for block in output_parts:
        block_type = block.get("type", "")
        if block_type == "text":
            parts.append({"type": "text", "content": block.get("text", "")})
        elif block_type == "tool_use":
            parts.append({
                "type": "tool_call",
                "id": block.get("id", ""),
                "name": block.get("name", ""),
                "arguments": block.get("input"),
            })
    return [{"role": "assistant", "parts": parts}]


def set_turn_span_response(
    span,
    msg: "AssistantMessage",
    chat: "Chat",
    output_parts: list[dict],
) -> None:
    """Set gen_ai response attributes on the turn span.

    Called from Chat._on_claude_event when ResultEvent arrives.
    Reads from the AssistantMessage for token counts and metadata,
    from the Chat for quota usage, and from output_parts for the
    Logfire gen_ai.output.messages format.
    """
    output_messages = format_output_messages(output_parts)

    span.set_attribute("gen_ai.response.model", msg.model or "")
    span.set_attribute("gen_ai.usage.input_tokens", msg.input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", msg.output_tokens)
    span.set_attribute("gen_ai.usage.cache_creation.input_tokens", msg.cache_creation_tokens)
    span.set_attribute("gen_ai.usage.cache_read.input_tokens", msg.cache_read_tokens)
    span.set_attribute("gen_ai.output.messages", output_messages)
    span.set_attribute("gen_ai.response.id", chat.response_id or "")
    span.set_attribute("gen_ai.response.finish_reasons", [msg.stop_reason or "unknown"])
    span.set_attribute("gen_ai.token_count", msg.input_tokens)
    span.set_attribute("cost_usd", msg.cost_usd)
    span.set_attribute("duration_ms", msg.duration_ms)
    span.set_attribute("inference_count", msg.inference_count)
    span.set_attribute("response_length", sum(
        len(p.get("content", ""))
        for m in output_messages
        for p in m.get("parts", [])
    ))


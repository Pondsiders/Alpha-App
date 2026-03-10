"""spans.py — Logfire span helpers for gen_ai semantic conventions.

Formats input/output messages and sets response attributes on turn spans
following the OpenTelemetry gen_ai conventions that Logfire understands.
"""

from alpha_app import ResultEvent

from alpha_app.chat import Chat


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


def set_turn_span_response(span, chat: Chat, result: ResultEvent, output_parts: list) -> None:
    """Set gen_ai response attributes on the turn span."""
    span.set_attribute("gen_ai.response.model", chat.response_model or "")
    span.set_attribute("gen_ai.usage.input_tokens", chat.total_input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", chat.output_tokens)
    span.set_attribute("gen_ai.usage.cache_creation.input_tokens", chat.cache_creation_tokens)
    span.set_attribute("gen_ai.usage.cache_read.input_tokens", chat.cache_read_tokens)

    output_messages = format_output_messages(output_parts)
    span.set_attribute("gen_ai.output.messages", output_messages)

    span.set_attribute("gen_ai.response.id", chat.response_id or "")
    span.set_attribute("gen_ai.response.finish_reasons", [chat.stop_reason or "unknown"])
    span.set_attribute("gen_ai.token_count", chat.token_count)
    span.set_attribute("cost_usd", result.cost_usd)
    span.set_attribute("duration_ms", result.duration_ms)
    span.set_attribute("inference_count", result.num_turns)
    span.set_attribute("response_length", sum(
        len(p.get("content", ""))
        for msg in output_messages
        for p in msg.get("parts", [])
    ))

    if chat.usage_5h is not None:
        span.set_attribute("anthropic.quota.usage_5h", chat.usage_5h)
    if chat.usage_7d is not None:
        span.set_attribute("anthropic.quota.usage_7d", chat.usage_7d)

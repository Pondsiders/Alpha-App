"""spans.py — Logfire span helpers for gen_ai semantic conventions.

Formats input/output messages for Logfire Model Run cards.
The turn-level span attributes are now set by streaming.py's
_set_turn_span_response(), which reads from AssistantMessage.
"""


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



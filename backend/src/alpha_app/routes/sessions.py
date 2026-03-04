"""Sessions route — list sessions, load content from JSONL.

GET /api/chats/{chat_id}/messages loads message history via chatId → session UUID → JSONL.
GET /api/sessions/{session_id} loads message history from JSONL files directly.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from alpha_app.db import get_pool

log = logging.getLogger(__name__)

router = APIRouter()

# Claude Code stores sessions as JSONL files at ~/.claude/projects/{cwd-with-dashes}/.
# Inside the container, claude's CWD is the container's CWD (usually /app).
_cwd = os.environ.get("ALPHA_CWD", os.getcwd())
_formatted_cwd = os.path.realpath(_cwd).replace("/", "-")
SESSIONS_DIR = Path(
    os.getenv(
        "SESSIONS_DIR",
        os.path.expanduser(f"~/.claude/projects/{_formatted_cwd}"),
    )
)


def extract_display_messages(lines: list[str]) -> list[dict[str, Any]]:
    """Extract user and assistant messages from JSONL records.

    Alpha doesn't have SDK-injected memory blocks yet, so messages
    are simpler. Just extract user text and assistant content blocks.
    """
    messages: list[dict[str, Any]] = []
    tool_calls_by_id: dict[str, dict[str, Any]] = {}

    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        record_type = record.get("type")

        if record_type == "user":
            content = record.get("message", {}).get("content", "")

            if isinstance(content, str):
                parts = [{"type": "text", "text": content}]
                messages.append({"role": "user", "content": parts})
            elif isinstance(content, list):
                parts: list[dict[str, Any]] = []
                for block in content:
                    if isinstance(block, str):
                        parts.append({"type": "text", "text": block})
                    elif isinstance(block, dict):
                        block_type = block.get("type")
                        if block_type == "text":
                            parts.append({"type": "text", "text": block.get("text", "")})
                        elif block_type == "image":
                            # Reconstruct data URI from Claude API base64 format
                            source = block.get("source", {})
                            if source.get("type") == "base64" and source.get("data"):
                                media_type = source.get("media_type", "image/png")
                                data_uri = f"data:{media_type};base64,{source['data']}"
                                parts.append({"type": "image", "image": data_uri})
                        elif block_type == "tool_result":
                            tool_use_id = block.get("tool_use_id")
                            result_content = block.get("content", "")
                            if isinstance(result_content, str):
                                result_text = result_content
                            elif isinstance(result_content, list):
                                texts = []
                                for r in result_content:
                                    if isinstance(r, dict) and r.get("type") == "text":
                                        texts.append(r.get("text", ""))
                                    elif isinstance(r, str):
                                        texts.append(r)
                                result_text = "\n".join(texts)
                            else:
                                result_text = str(result_content)
                            if tool_use_id and tool_use_id in tool_calls_by_id:
                                tool_calls_by_id[tool_use_id]["result"] = result_text
                if parts:
                    messages.append({"role": "user", "content": parts})
            else:
                parts = [{"type": "text", "text": str(content)}]
                messages.append({"role": "user", "content": parts})

        elif record_type == "assistant":
            content_blocks = record.get("message", {}).get("content", [])
            parts: list[dict[str, Any]] = []

            for block in content_blocks:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append({"type": "text", "text": block.get("text", "")})
                    elif block.get("type") == "tool_use":
                        tool_input = block.get("input", {})
                        tool_call = {
                            "type": "tool-call",
                            "toolCallId": block.get("id"),
                            "toolName": block.get("name"),
                            "args": tool_input,
                            "argsText": json.dumps(tool_input, indent=2),
                        }
                        parts.append(tool_call)
                        tool_id = block.get("id")
                        if tool_id:
                            tool_calls_by_id[tool_id] = tool_call

            if parts:
                messages.append({"role": "assistant", "content": parts})

    return messages


@router.get("/api/chats/{chat_id}/messages")
async def get_chat_messages(chat_id: str) -> dict[str, Any]:
    """Load a chat's message history. Maps chatId → session UUID → JSONL."""
    # Look up session UUID from Postgres
    try:
        pool = get_pool()
        row = await pool.fetchrow(
            "SELECT data FROM app.chats WHERE id = $1",
            chat_id,
        )
    except Exception as e:
        log.exception("Postgres lookup failed: %s", e)
        raise HTTPException(status_code=500, detail="Database error")

    if not row:
        return {"chatId": chat_id, "messages": []}

    session_uuid = row["data"].get("session_uuid")
    if not session_uuid:
        # Chat exists but has no session yet (never completed a turn)
        return {"chatId": chat_id, "messages": []}

    jsonl_path = SESSIONS_DIR / f"{session_uuid}.jsonl"
    if not jsonl_path.exists():
        return {"chatId": chat_id, "messages": []}

    content = jsonl_path.read_text()
    lines = [line for line in content.split("\n") if line.strip()]
    messages = extract_display_messages(lines)

    return {"chatId": chat_id, "messages": messages}


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    """Load a session's message history from JSONL."""
    jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"

    if not jsonl_path.exists():
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    content = jsonl_path.read_text()
    lines = [line for line in content.split("\n") if line.strip()]

    messages = extract_display_messages(lines)

    # Get metadata from first/last records
    first = json.loads(lines[0]) if lines else {}
    last = json.loads(lines[-1]) if lines else {}

    return {
        "session_id": session_id,
        "messages": messages,
        "created_at": first.get("timestamp"),
        "updated_at": last.get("timestamp"),
    }

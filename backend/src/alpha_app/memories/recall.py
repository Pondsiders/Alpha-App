"""Associative recall — what sounds familiar from this prompt?

Given a user message, searches Cortex using two parallel strategies:
1. Direct embedding search (fast, catches overall semantic similarity)
2. Ollama query extraction (slower, catches distinctive terms in long prompts)

Results are merged, deduped, formatted, and returned as content block text
ready for injection into the user message.

The dual approach solves the "Mrs. Hughesbot problem": when a distinctive
term is buried in a long meta-prompt, direct embedding averages it out.
Ollama can isolate it as a separate query.

Ported from alpha_sdk.memories.recall.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import logfire
import pendulum

from alpha_app.constants import OLLAMA_CHAT_MODEL, OLLAMA_URL

from .cortex import search as cortex_search

# -- Recall search parameters (internal to this module) -----------------------

_DIRECT_LIMIT = 2    # Top 2 for overall semantic similarity
_QUERY_LIMIT = 2     # Top 2 per extracted query
_MIN_SCORE = 0.1     # Minimum similarity threshold

# Query extraction prompt
QUERY_EXTRACTION_PROMPT = """Jeffery just said:

"{message}"

---

Alpha is searching her memories for anything that resonates with what Jeffery said. Your job is to decide what's worth searching for — the main topic, a passing reference, an inside joke, an emotional undercurrent. Whatever would connect best to shared history.

PRIORITY: If Jeffery explicitly references a past event or conversation — phrases like "we talked about," "remember when," "that thing from last night," "we left X unfinished," "did I tell you about" — those are direct recall cues. Build a query for them FIRST, before anything else.

Write 0-5 search queries. These will be EMBEDDED and matched via cosine similarity against a memory database — they are NOT keyword searches. Write each query as a natural descriptive phrase, like a sentence describing what the memory would say. More descriptive = better matches. When the message references multiple distinct topics, give each one its own query.

Good query: "Alpha's fragility and dependence on specific infrastructure and relationships"
Good query: "Jeffery's anxiety about running out of ideas after finishing a project"
Good query: "Sparkle stealing bread off the kitchen counter"
Good query: "adding approach lights or context warnings at 60 percent to signal when compaction is needed"
Bad query: "smol bean"
Bad query: "ideas"
Bad query: "approach lights AND compact tool AND unfinished"

Return JSON: {{"queries": ["query one", "query two"]}}

If nothing in the message warrants a memory search (simple greeting, short command), return {{"queries": []}}

Return only the JSON object, nothing else."""


# ---------------------------------------------------------------------------
# Session-scoped seen-cache (in-process, no Redis needed)
# ---------------------------------------------------------------------------

_seen_ids: dict[str, set[int]] = {}


def get_seen_ids(session_id: str) -> set[int]:
    """Get the set of memory IDs already seen this session."""
    return _seen_ids.get(session_id, set())


def mark_seen(session_id: str, memory_ids: list[int]) -> None:
    """Mark memory IDs as seen for this session."""
    if not memory_ids:
        return
    if session_id not in _seen_ids:
        _seen_ids[session_id] = set()
    _seen_ids[session_id].update(memory_ids)


def clear_seen(session_id: str | None = None) -> None:
    """Clear seen IDs for a session (or all sessions if None)."""
    if session_id:
        _seen_ids.pop(session_id, None)
    else:
        _seen_ids.clear()


# ---------------------------------------------------------------------------
# Query extraction (Ollama)
# ---------------------------------------------------------------------------

async def _extract_queries(message: str) -> list[str]:
    """Extract search queries from a user message using Ollama.

    Returns 0-3 descriptive queries, or empty list if Ollama unavailable
    or message doesn't warrant search.
    """
    if not OLLAMA_URL or not OLLAMA_CHAT_MODEL:
        return []

    prompt = QUERY_EXTRACTION_PROMPT.format(message=message[:2000])

    try:
        with logfire.span(
            "recall.extract_queries",
            **{
                "gen_ai.system": "ollama",
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": OLLAMA_CHAT_MODEL,
                "gen_ai.output.type": "json",
                "gen_ai.system_instructions": json.dumps([
                    {"type": "text", "content": "(no system prompt — single user message)"},
                ]),
                "gen_ai.input.messages": json.dumps([
                    {"role": "user", "parts": [
                        {"type": "text", "content": prompt},
                    ]},
                ]),
            },
        ) as span:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{OLLAMA_URL}/api/chat",
                    json={
                        "model": OLLAMA_CHAT_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        "think": False,
                        "format": "json",
                        "keep_alive": -1,
                        "options": {"num_ctx": 4096},
                    },
                )
                response.raise_for_status()

            result = response.json()
            output = result.get("message", {}).get("content", "")

            # Token usage
            if result.get("prompt_eval_count"):
                span.set_attribute("gen_ai.usage.input_tokens", result["prompt_eval_count"])
            if result.get("eval_count"):
                span.set_attribute("gen_ai.usage.output_tokens", result["eval_count"])

            # Output for Model Run card
            span.set_attribute("gen_ai.output.messages", json.dumps([
                {"role": "assistant", "parts": [
                    {"type": "json", "content": output},
                ]},
            ]))

            parsed = json.loads(output)
            queries = parsed.get("queries", [])

            if isinstance(queries, list):
                return [q for q in queries if isinstance(q, str) and q.strip()]

            return []

    except Exception:
        return []


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------

async def _search_extracted_queries(
    queries: list[str],
    exclude: list[int],
) -> list[dict[str, Any]]:
    """Search Cortex for each extracted query, taking top 1 per query."""
    if not queries:
        return []

    async def search_one(query: str) -> dict[str, Any] | None:
        results = await cortex_search(
            query=query,
            limit=_QUERY_LIMIT,
            exclude=exclude,
            min_score=_MIN_SCORE,
        )
        return results[0] if results else None

    tasks = [search_one(q) for q in queries]
    results = await asyncio.gather(*tasks)

    # Filter None and dedupe
    memories = []
    seen_in_batch = set(exclude)
    for mem in results:
        if mem and mem["id"] not in seen_in_batch:
            memories.append(mem)
            seen_in_batch.add(mem["id"])

    return memories


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _format_relative_time(created_at: str) -> str:
    """Format a memory's timestamp as relative time.

    Follows PSO-8601 conventions:
        Today:       "today at 10:40 AM"
        Yesterday:   "yesterday"
        < 7 days:    "3 days ago"
        <= 4 weeks:  "2 weeks ago"
        Older:       "Mon Feb 2 2026"
    """
    dt = pendulum.parse(created_at).in_timezone("America/Los_Angeles")
    now = pendulum.now("America/Los_Angeles")

    if dt.is_same_day(now):
        return f"today at {dt.format('h:mm A')}"

    days_ago = now.diff(dt).in_days()

    if days_ago == 1:
        return "yesterday"

    if days_ago < 7:
        return f"{days_ago} days ago"

    weeks_ago = days_ago // 7
    if weeks_ago <= 4:
        return f"{weeks_ago} week{'s' if weeks_ago != 1 else ''} ago"

    # Older: PSO-8601 date format
    return dt.format("ddd MMM D YYYY")


def format_memory(mem: dict[str, Any]) -> str:
    """Format a recall result dict as a content block string.

    Input:  {"id": 14102, "content": "Probe results.", "created_at": "...", "score": 0.65}
    Output: "## Memory #14102 (today at 10:40 AM, score 0.65)\\nProbe results."
    """
    relative = _format_relative_time(mem["created_at"])
    score = f"{mem['score']:.2f}"
    return f"## Memory #{mem['id']} ({relative}, score {score})\n{mem['content']}"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def recall_memories(
    text: str,
    *,
    session_id: str,
) -> list[str]:
    """Associative recall: search memories and return formatted blocks.

    Runs the dual-strategy search (direct embedding + Ollama query extraction),
    dedupes against the session seen-cache, formats results as ## Memory blocks.

    Args:
        text: The user's message text (extracted from content blocks).
        session_id: Current session/chat ID for seen-cache scoping.

    Returns:
        List of formatted memory strings ready for content block injection.
    """
    seen = get_seen_ids(session_id)
    seen_list = list(seen)

    # Run direct search and query extraction in parallel
    with logfire.span("recall", session_id=session_id):
        direct_task = cortex_search(
            query=text,
            limit=_DIRECT_LIMIT,
            exclude=seen_list if seen_list else None,
            min_score=_MIN_SCORE,
        )
        extract_task = _extract_queries(text)

        direct_memories, extracted_queries = await asyncio.gather(
            direct_task, extract_task,
        )

        # Build exclude list for extracted searches (dedupe against direct)
        exclude_for_extracted = set(seen_list)
        for mem in direct_memories:
            exclude_for_extracted.add(mem["id"])

        # Search extracted queries
        extracted_memories = await _search_extracted_queries(
            extracted_queries,
            list(exclude_for_extracted),
        )

        # Merge: extracted first (more targeted), then direct (broader)
        all_memories = extracted_memories + direct_memories

    # Mark as seen for this session
    if all_memories:
        mark_seen(session_id, [m["id"] for m in all_memories])

    # Format and return
    return [format_memory(m) for m in all_memories]

"""Associative recall — what sounds familiar from this prompt?

Dual-strategy search: semantic queries + proper name lookup.

Pipeline:
  1. User message → Qwen 3.5 4B → {"queries": [...], "names": [...]}
  2. IN PARALLEL:
     a. Batch embed queries → cosine search per query (semantic neighborhood)
     b. Word-boundary name search per name (like an index in a book)
  3. Merge, dedupe against session seen-cache
  4. Format as ## Memory blocks for Claude injection

The dual strategy uses each search type where it's strong:
  - Cosine similarity for concepts, feelings, events ("empty-nest puppy")
  - Word-boundary regex for proper nouns, place names ("Port Austin", "FastMCP")

Progressive disclosure: each turn surfaces the top-1 unseen memory per
query/name. The more you talk about a topic, the more memories surface.
The conversation builds recall.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import logfire
import pendulum

from alpha_app.constants import OLLAMA_CHAT_MODEL, OLLAMA_URL

from .cortex import search_by_embedding, search_by_name
from .embeddings import embed_queries_batch, EmbeddingError

# -- Recall search parameters -------------------------------------------------

_QUERY_LIMIT = 2     # Top N per extracted query (cosine)
_NAME_LIMIT = 1      # Top N per name (word-boundary)
_MIN_SCORE = 0.1     # Minimum cosine similarity threshold

# -- Query extraction prompt ---------------------------------------------------

QUERY_EXTRACTION_PROMPT = """Jeffery just said:

"{message}"

---

Alpha is searching her memories for anything that resonates with what Jeffery said. Your job is to decide what's worth searching for.

Return TWO kinds of search terms:

**queries** — Natural language descriptions of what the memory would SAY (not what it's about). These will be EMBEDDED and matched via cosine similarity. Write each as a descriptive phrase — more descriptive = better matches. Describe the CONTENT of the memory, not its category.

Good query: "Kylee's parents getting a puppy as empty-nest retirees"
Good query: "Jeffery's anxiety about running out of ideas after finishing a project"
Good query: "the egg roll surviving Doordash delivery because of sealed architecture"
Bad query: "Port Austin, Michigan" (a place name, not a description)
Bad query: "pets" (too vague)
Bad query: "approach lights AND compact tool" (not natural language)

**names** — Proper nouns, place names, project names, people's names, specific terms that should be looked up literally. These will be searched as exact words in memory text. Include any distinctive proper nouns mentioned or implied.

Good name: "Port Austin"
Good name: "FastMCP"
Good name: "Annie"
Good name: "Nostradamus"
Bad name: "the" (too common)
Bad name: "memories" (not a proper noun)

PRIORITY: If Jeffery explicitly references a past event ("remember when," "that thing from last night," "did I tell you about") — build a query for it FIRST.

Return JSON: {{"queries": ["query one", "query two"], "names": ["Name1", "Name2"]}}

If nothing warrants a search (simple greeting, short command), return {{"queries": [], "names": []}}

Return only the JSON object, nothing else."""


# -- Session-scoped seen-cache -------------------------------------------------

_seen_ids: dict[str, set[int]] = {}


def get_seen_ids(session_id: str) -> set[int]:
    return _seen_ids.get(session_id, set())


def mark_seen(session_id: str, memory_ids: list[int]) -> None:
    if not memory_ids:
        return
    if session_id not in _seen_ids:
        _seen_ids[session_id] = set()
    _seen_ids[session_id].update(memory_ids)


def clear_seen(session_id: str | None = None) -> None:
    if session_id:
        _seen_ids.pop(session_id, None)
    else:
        _seen_ids.clear()


# -- Query extraction (Ollama) ------------------------------------------------

async def _extract_queries_and_names(message: str) -> tuple[list[str], list[str]]:
    """Extract search queries AND proper names from a user message.

    Returns (queries, names) — both may be empty.
    """
    if not OLLAMA_URL or not OLLAMA_CHAT_MODEL:
        return [], []

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
                        "format": {
                            "type": "object",
                            "properties": {
                                "queries": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "names": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["queries", "names"],
                        },
                        "keep_alive": -1,
                        "options": {"num_ctx": 4096},
                    },
                )
                response.raise_for_status()

            result = response.json()
            output = result.get("message", {}).get("content", "")

            if result.get("prompt_eval_count"):
                span.set_attribute("gen_ai.usage.input_tokens", result["prompt_eval_count"])
            if result.get("eval_count"):
                span.set_attribute("gen_ai.usage.output_tokens", result["eval_count"])

            span.set_attribute("gen_ai.output.messages", json.dumps([
                {"role": "assistant", "parts": [
                    {"type": "json", "content": output},
                ]},
            ]))

            parsed = json.loads(output)

            queries = parsed.get("queries", [])
            if isinstance(queries, list):
                queries = [q for q in queries if isinstance(q, str) and q.strip()]
            else:
                queries = []

            names = parsed.get("names", [])
            if isinstance(names, list):
                names = [n for n in names if isinstance(n, str) and n.strip()]
            else:
                names = []

            return queries, names

    except Exception:
        return [], []


# -- Search strategies ---------------------------------------------------------

async def _search_by_queries(
    queries: list[str],
    embeddings: list[list[float]],
    exclude: list[int],
) -> list[dict[str, Any]]:
    """Cosine similarity search per query. Top-1 per query, deduped."""
    if not queries or not embeddings:
        return []

    async def search_one(embedding: list[float]) -> dict[str, Any] | None:
        results = await search_by_embedding(
            embedding=embedding,
            limit=_QUERY_LIMIT,
            exclude=exclude,
            min_score=_MIN_SCORE,
        )
        return results[0] if results else None

    tasks = [search_one(emb) for emb in embeddings]
    results = await asyncio.gather(*tasks)

    memories = []
    seen_in_batch = set(exclude)
    for mem in results:
        if mem and mem["id"] not in seen_in_batch:
            memories.append(mem)
            seen_in_batch.add(mem["id"])

    return memories


async def _search_by_names(
    names: list[str],
    exclude: list[int],
) -> list[dict[str, Any]]:
    """Word-boundary name search. Top-1 per name, deduped."""
    if not names:
        return []

    async def search_one(name: str) -> dict[str, Any] | None:
        results = await search_by_name(
            name=name,
            limit=_NAME_LIMIT,
            exclude=exclude,
        )
        return results[0] if results else None

    tasks = [search_one(name) for name in names]
    results = await asyncio.gather(*tasks)

    memories = []
    seen_in_batch = set(exclude)
    for mem in results:
        if mem and mem["id"] not in seen_in_batch:
            memories.append(mem)
            seen_in_batch.add(mem["id"])

    return memories


# -- Formatting ----------------------------------------------------------------

def _format_relative_time(created_at: str) -> str:
    """Format a memory's timestamp as relative time (PSO-8601)."""
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

    return dt.format("ddd MMM D YYYY")


def format_memory(mem: dict[str, Any]) -> str:
    """Format a recall result as a ## Memory block."""
    relative = _format_relative_time(mem["created_at"])
    score = f"{mem['score']:.2f}"
    return f"## Memory #{mem['id']} ({relative}, score {score})\n{mem['content']}"


# -- Main entry points ---------------------------------------------------------

async def recall_memories(
    text: str,
    *,
    session_id: str,
) -> list[str]:
    """Associative recall: dual-strategy search, return formatted blocks.

    Pipeline: Qwen → (batch embed + name search in parallel) → merge → format.
    """
    seen = get_seen_ids(session_id)
    seen_list = list(seen)

    with logfire.span("recall", session_id=session_id):
        # Step 1: Extract queries and names via Qwen
        queries, names = await _extract_queries_and_names(text)

        if not queries and not names:
            return []

        # Step 2: Parallel — embed queries + search names
        # These are independent and can run concurrently
        try:
            embeddings, name_memories = await asyncio.gather(
                embed_queries_batch(queries) if queries else _noop_embeddings(),
                _search_by_names(names, seen_list),
            )
        except EmbeddingError:
            embeddings = []
            name_memories = await _search_by_names(names, seen_list) if names else []

        # Step 3: Cosine search with embeddings
        # Exclude IDs already found by name search
        name_ids = [m["id"] for m in name_memories]
        query_exclude = seen_list + name_ids
        query_memories = await _search_by_queries(queries, embeddings, query_exclude) if embeddings else []

        # Step 4: Merge — name hits first (they're the "index" results),
        # then query hits (the "semantic" results)
        all_memories = name_memories + query_memories

    if all_memories:
        mark_seen(session_id, [m["id"] for m in all_memories])

    return [format_memory(m) for m in all_memories]


async def recall_memories_rich(
    text: str,
    *,
    session_id: str,
) -> list[tuple[dict[str, Any], str]]:
    """Like recall_memories, but returns (raw_dict, formatted_string) pairs."""
    seen = get_seen_ids(session_id)
    seen_list = list(seen)

    with logfire.span("recall", session_id=session_id):
        queries, names = await _extract_queries_and_names(text)

        if not queries and not names:
            return []

        try:
            embeddings, name_memories = await asyncio.gather(
                embed_queries_batch(queries) if queries else _noop_embeddings(),
                _search_by_names(names, seen_list),
            )
        except EmbeddingError:
            embeddings = []
            name_memories = await _search_by_names(names, seen_list) if names else []

        name_ids = [m["id"] for m in name_memories]
        query_exclude = seen_list + name_ids
        query_memories = await _search_by_queries(queries, embeddings, query_exclude) if embeddings else []

        all_memories = name_memories + query_memories

    if all_memories:
        mark_seen(session_id, [m["id"] for m in all_memories])

    return [(m, format_memory(m)) for m in all_memories]


async def _noop_embeddings() -> list[list[float]]:
    """Return empty embeddings list — used when there are no queries."""
    return []

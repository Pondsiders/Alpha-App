"""Associative recall — what sounds familiar from this prompt?

Dual-strategy search with unified IDF/cosine scoring.

Pipeline:
  1. User message → Qwen 3.5 4B → {"queries": [...], "names": [...]}
  2. IN PARALLEL:
     a. Batch embed queries (one Ollama call)
     b. Compute IDF for each name → filter out IDF < 1.0 (too common)
  3. IN PARALLEL:
     a. Cosine search per query (score = cosine similarity, 0-1 range)
     b. Name search per surviving name (score = IDF, 1.0+ range)
  4. Merge all results, dedupe, sort by score descending

The IDF IS the score for name hits. Names that appear rarely (high IDF)
rank above cosine hits. Names that appear everywhere (low IDF) get
filtered out entirely. Cosine hits fill in the vibes. One unified
ranking by informativeness.

Uncapped: the natural filters (IDF cutoff, seen-cache, deduplication)
keep the count reasonable. Progressive disclosure across turns — the
more you talk about a topic, the more memories surface.
"""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any

import httpx
import logfire
import pendulum

from alpha_app.constants import OLLAMA_CHAT_MODEL, OLLAMA_URL

from .cortex import search_by_embedding, search_by_name, count_memories_containing
from .embeddings import embed_queries_batch, EmbeddingError

# -- Constants -----------------------------------------------------------------

_QUERY_LIMIT = 2     # Top N per cosine query
_NAME_LIMIT = 1      # Top N per name search
_MIN_COSINE = 0.1    # Minimum cosine similarity threshold
_MIN_IDF = 1.0       # Names with IDF below this are too common to search

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
    """Extract search queries AND proper names from a user message."""
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

            # Strip markdown code fences if present. The schema constraint
            # usually prevents this, but Qwen occasionally sneaks through.
            cleaned = output.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3].strip()

            parsed = json.loads(cleaned)

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


# -- IDF computation -----------------------------------------------------------

async def _compute_idf(name: str, total_memories: int) -> tuple[str, float]:
    """Compute IDF for a name. Returns (name, idf_score)."""
    try:
        count = await count_memories_containing(name)
        if count == 0:
            # Name not found in any memory — maximum informativeness
            # but also means the search will return nothing. Skip.
            return name, 0.0
        idf = math.log(total_memories / count)
        return name, idf
    except Exception:
        return name, 0.0


async def _get_total_memory_count() -> int:
    """Get total number of non-forgotten memories for IDF denominator."""
    from .db import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM cortex.memories WHERE NOT forgotten"
        )


# -- Search strategies ---------------------------------------------------------

async def _search_by_queries(
    embeddings: list[list[float]],
    exclude: list[int],
) -> list[dict[str, Any]]:
    """Cosine similarity search per query. Top-1 per query, deduped."""
    if not embeddings:
        return []

    async def search_one(embedding: list[float]) -> dict[str, Any] | None:
        results = await search_by_embedding(
            embedding=embedding,
            limit=_QUERY_LIMIT,
            exclude=exclude,
            min_score=_MIN_COSINE,
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


async def _search_by_names_with_idf(
    names: list[str],
    exclude: list[int],
) -> list[dict[str, Any]]:
    """Name search with IDF scoring. Filters out common names, scores rare ones."""
    if not names:
        return []

    # Get total memory count for IDF denominator
    total = await _get_total_memory_count()

    # Compute IDF for all names in parallel
    idf_tasks = [_compute_idf(name, total) for name in names]
    idf_results = await asyncio.gather(*idf_tasks)

    # Filter: keep only names with IDF >= threshold
    informative_names = [
        (name, idf) for name, idf in idf_results if idf >= _MIN_IDF
    ]

    logfire.debug(
        "recall.idf: {filtered}/{total_names} names passed IDF filter",
        filtered=len(informative_names),
        total_names=len(names),
        idf_scores={name: round(idf, 2) for name, idf in idf_results},
    )

    if not informative_names:
        return []

    # Search for each surviving name
    async def search_one(name: str, idf: float) -> dict[str, Any] | None:
        results = await search_by_name(
            name=name,
            limit=_NAME_LIMIT,
            exclude=exclude,
        )
        if results:
            mem = results[0]
            mem["score"] = idf  # Replace the binary 1.0 with the IDF score
            return mem
        return None

    tasks = [search_one(name, idf) for name, idf in informative_names]
    results = await asyncio.gather(*tasks)

    # Dedupe within batch
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

async def _recall_core(
    text: str,
    session_id: str,
) -> list[dict[str, Any]]:
    """Core recall logic — returns raw memory dicts sorted by score."""
    seen = get_seen_ids(session_id)
    seen_list = list(seen)

    with logfire.span("recall", session_id=session_id):
        # Step 1: Extract queries and names via Qwen
        queries, names = await _extract_queries_and_names(text)

        if not queries and not names:
            return []

        # Step 2: IN PARALLEL — batch embed queries + compute IDF for names
        try:
            embeddings, name_memories = await asyncio.gather(
                embed_queries_batch(queries) if queries else _noop_embeddings(),
                _search_by_names_with_idf(names, seen_list),
            )
        except EmbeddingError:
            embeddings = []
            name_memories = await _search_by_names_with_idf(names, seen_list) if names else []

        # Step 3: Cosine search (exclude IDs already found by name search)
        name_ids = [m["id"] for m in name_memories]
        query_exclude = seen_list + name_ids
        query_memories = await _search_by_queries(embeddings, query_exclude) if embeddings else []

        # Step 4: Merge and sort by score (IDF scores > 1.0 naturally rank above cosine)
        all_memories = name_memories + query_memories
        all_memories.sort(key=lambda m: m.get("score", 0), reverse=True)

    # Mark as seen for this session
    if all_memories:
        mark_seen(session_id, [m["id"] for m in all_memories])

    return all_memories


async def recall_memories(
    text: str,
    *,
    session_id: str,
) -> list[str]:
    """Associative recall: return formatted memory blocks."""
    memories = await _recall_core(text, session_id)
    return [format_memory(m) for m in memories]


async def recall_memories_rich(
    text: str,
    *,
    session_id: str,
) -> list[tuple[dict[str, Any], str]]:
    """Like recall_memories, but returns (raw_dict, formatted_string) pairs."""
    memories = await _recall_core(text, session_id)
    return [(m, format_memory(m)) for m in memories]


async def _noop_embeddings() -> list[list[float]]:
    """Return empty embeddings list — used when there are no queries."""
    return []

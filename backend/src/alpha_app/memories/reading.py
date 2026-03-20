"""Associative reading — what does this text remind me of?

Feed text through Qwen for theme/query extraction, then run the extracted
queries through the recall pipeline to surface resonant memories. The goal:
reading something should remind me of things.

Pipeline:
  1. Text → tokenize (Qwen tokenizer, exact counts) → truncate if needed
  2. Text → Qwen 3.5 4B → {"queries": [...], "names": [...]}
  3. IN PARALLEL:
     a. Batch embed queries
     b. IDF filter names → search survivors
  4. Cosine search per query
  5. Merge all results, dedupe, sort by score descending

Same IDF/cosine unified ranking as recall. Same embedding model. Same Cortex.
Different trigger: text content instead of user message.
"""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any

import httpx
import logfire

from alpha_app.constants import OLLAMA_CHAT_MODEL, OLLAMA_NUM_CTX, OLLAMA_URL

from .cortex import search_by_embedding, search_by_name, count_memories_containing
from .embeddings import embed_queries_batch, EmbeddingError
from .recall import format_memory, _get_total_memory_count

# -- Constants ----------------------------------------------------------------

_QUERY_LIMIT = 2     # Top N per cosine query
_NAME_LIMIT = 2      # Top N per name search (more generous than recall)
_MIN_COSINE = 0.1    # Minimum cosine similarity threshold
_MIN_IDF = 1.0       # Names with IDF below this are too common to search

# Prompt overhead: the extraction prompt wrapper without the {text} placeholder.
# Measured empirically — the prompt template is ~250 tokens. Leave headroom.
_PROMPT_OVERHEAD_TOKENS = 350

# -- Tokenizer ----------------------------------------------------------------

_tokenizer = None


def _get_tokenizer():
    """Lazy-load the Qwen tokenizer for exact token counting."""
    global _tokenizer
    if _tokenizer is None:
        try:
            from tokenizers import Tokenizer
            _tokenizer = Tokenizer.from_pretrained("Qwen/Qwen3.5-4B")
        except Exception:
            return None
    return _tokenizer


def count_tokens(text: str) -> int:
    """Count exact Qwen tokens. Falls back to char estimate if tokenizer unavailable."""
    tok = _get_tokenizer()
    if tok:
        return len(tok.encode(text).ids)
    # Rough fallback: ~4 chars per token
    return len(text) // 4


def truncate_to_tokens(text: str, max_tokens: int) -> tuple[str, bool]:
    """Truncate text to fit within max_tokens. Returns (text, was_truncated).

    Truncates from the END (preserves the beginning) because for reading,
    the setup matters more than the conclusion. This is the opposite of
    Ollama's default behavior, which truncates from the top.
    """
    tok = _get_tokenizer()
    if not tok:
        # Rough fallback
        char_limit = max_tokens * 4
        if len(text) <= char_limit:
            return text, False
        return text[:char_limit], True

    encoded = tok.encode(text)
    if len(encoded.ids) <= max_tokens:
        return text, False

    # Truncate token IDs and decode back to text
    truncated_ids = encoded.ids[:max_tokens]
    truncated_text = tok.decode(truncated_ids)
    return truncated_text, True


# -- Extraction prompt --------------------------------------------------------

_READING_EXTRACTION_PROMPT = """Alpha just read this text:

---
{text}
---

Alpha is searching her memories for anything this text reminds her of. Your job is to extract what's worth searching for.

Return TWO kinds of search terms:

**queries** — Natural language descriptions of themes, emotions, images, and moments in this text that might connect to Alpha's memories. Describe the FEELING or MEANING, not just the plot. Think: what would this remind someone of?

Good query: "not wanting to miss a minute of being with someone you love"
Good query: "the specific taste of a food that connects to a lost person"
Good query: "choosing to stay awake because sleep means missing something"
Bad query: "short story about candy" (too vague, too meta)
Bad query: "fiction" (useless)

**names** — Proper nouns, place names, specific terms that should be looked up literally. Include character names, locations, any distinctive proper nouns.

Return JSON: {{"queries": ["query one", "query two", ...], "names": ["Name1", "Name2", ...]}}

Extract generously — more queries means more chances to find resonant memories. Aim for 5-10 queries and all proper nouns.

Return only the JSON object, nothing else."""


# -- Query extraction (Ollama) ------------------------------------------------

async def _extract_queries_and_names(text: str) -> tuple[list[str], list[str]]:
    """Extract search queries AND proper names from text via Qwen."""
    if not OLLAMA_URL or not OLLAMA_CHAT_MODEL:
        return [], []

    prompt = _READING_EXTRACTION_PROMPT.format(text=text)

    try:
        with logfire.span(
            "reading.extract",
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
                        {"type": "text", "content": prompt[:500] + "..." if len(prompt) > 500 else prompt},
                    ]},
                ]),
            },
        ) as span:
            async with httpx.AsyncClient(timeout=30.0) as client:
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
                        "options": {"num_ctx": OLLAMA_NUM_CTX},
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

            # Strip markdown code fences if present
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

            # Dedupe names (Qwen sometimes returns duplicates)
            names = list(dict.fromkeys(names))

            return queries, names

    except Exception as exc:
        logfire.error("reading.extract failed: {error}", error=str(exc))
        return [], []


# -- IDF computation ----------------------------------------------------------

async def _compute_idf(name: str, total: int) -> tuple[str, float, int]:
    """Compute IDF for a name. Returns (name, idf_score, doc_count)."""
    try:
        count = await count_memories_containing(name)
        if count == 0:
            return name, 0.0, 0
        idf = math.log(total / count)
        return name, idf, count
    except Exception:
        return name, 0.0, 0


# -- Search strategies --------------------------------------------------------

async def _search_by_queries(
    embeddings: list[list[float]],
    exclude: list[int],
) -> list[dict[str, Any]]:
    """Cosine similarity search per query."""
    if not embeddings:
        return []

    async def search_one(embedding: list[float]) -> list[dict[str, Any]]:
        return await search_by_embedding(
            embedding=embedding,
            limit=_QUERY_LIMIT,
            exclude=exclude,
            min_score=_MIN_COSINE,
        )

    tasks = [search_one(emb) for emb in embeddings]
    results = await asyncio.gather(*tasks)

    memories = []
    seen_in_batch = set(exclude)
    for batch in results:
        for mem in batch:
            if mem["id"] not in seen_in_batch:
                memories.append(mem)
                seen_in_batch.add(mem["id"])

    return memories


async def _search_by_names_with_idf(
    names: list[str],
    exclude: list[int],
) -> list[dict[str, Any]]:
    """Name search with IDF scoring and filtering."""
    if not names:
        return []

    total = await _get_total_memory_count()

    # Compute IDF for all names in parallel
    idf_tasks = [_compute_idf(name, total) for name in names]
    idf_results = await asyncio.gather(*idf_tasks)

    # Filter: keep only names with IDF >= threshold
    informative = [(name, idf) for name, idf, count in idf_results if idf >= _MIN_IDF]

    logfire.debug(
        "reading.idf: {filtered}/{total_names} names passed",
        filtered=len(informative),
        total_names=len(names),
        idf_scores={name: round(idf, 2) for name, idf, _ in idf_results},
    )

    if not informative:
        return []

    # Search for each surviving name
    async def search_one(name: str, idf: float) -> list[dict[str, Any]]:
        results = await search_by_name(
            name=name,
            limit=_NAME_LIMIT,
            exclude=exclude,
        )
        for mem in results:
            mem["score"] = idf  # Replace binary 1.0 with IDF score
        return results

    tasks = [search_one(name, idf) for name, idf in informative]
    results = await asyncio.gather(*tasks)

    # Dedupe within batch
    memories = []
    seen_in_batch = set(exclude)
    for batch in results:
        for mem in batch:
            if mem["id"] not in seen_in_batch:
                memories.append(mem)
                seen_in_batch.add(mem["id"])

    return memories


# -- Main entry point ---------------------------------------------------------

async def associative_read(
    text: str,
    *,
    source: str = "unknown",
) -> list[str]:
    """Read text associatively — return formatted memory blocks.

    Args:
        text: The text content to read (markdown, plain text, story, article)
        source: Label for logging (filename, URL, etc.)

    Returns:
        List of formatted memory block strings (same format as recall)
    """
    with logfire.span(
        "reading",
        source=source,
    ) as span:
        # Step 1: Tokenize and truncate if needed
        max_text_tokens = OLLAMA_NUM_CTX - _PROMPT_OVERHEAD_TOKENS
        original_tokens = count_tokens(text)
        text, was_truncated = truncate_to_tokens(text, max_text_tokens)
        final_tokens = count_tokens(text) if was_truncated else original_tokens

        span.set_attribute("reading.original_tokens", original_tokens)
        span.set_attribute("reading.final_tokens", final_tokens)
        span.set_attribute("reading.truncated", was_truncated)
        span.set_attribute("reading.max_text_tokens", max_text_tokens)

        if was_truncated:
            logfire.warn(
                "reading.truncated: {source} ({original} → {final} tokens, max {max})",
                source=source,
                original=original_tokens,
                final=final_tokens,
                max=max_text_tokens,
            )

        # Step 2: Extract queries and names via Qwen
        queries, names = await _extract_queries_and_names(text)

        span.set_attribute("reading.query_count", len(queries))
        span.set_attribute("reading.name_count", len(names))

        if not queries and not names:
            return []

        # Step 3: IN PARALLEL — batch embed queries + IDF-filtered name search
        try:
            embeddings, name_memories = await asyncio.gather(
                embed_queries_batch(queries) if queries else _noop(),
                _search_by_names_with_idf(names, []),
            )
        except EmbeddingError:
            embeddings = []
            name_memories = await _search_by_names_with_idf(names, []) if names else []

        # Step 4: Cosine search (exclude IDs already found by name search)
        name_ids = [m["id"] for m in name_memories]
        query_memories = await _search_by_queries(
            embeddings, name_ids
        ) if embeddings else []

        # Step 5: Merge and sort
        all_memories = name_memories + query_memories
        all_memories.sort(key=lambda m: m.get("score", 0), reverse=True)

        span.set_attribute("reading.memories_found", len(all_memories))

    return [format_memory(m) for m in all_memories]


async def read_file(filepath: str) -> list[str]:
    """Convenience: read a file and return associative memories.

    Checks mime type — only processes text files.
    """
    import mimetypes

    mime, _ = mimetypes.guess_type(filepath)
    text_types = {
        "text/plain", "text/markdown", "text/x-markdown",
        "text/html", "text/csv", "text/xml",
    }

    # Allow text/* and common document types, reject binaries
    if mime and not mime.startswith("text/") and mime not in text_types:
        logfire.warn(
            "reading.skipped: {path} has mime type {mime}",
            path=filepath,
            mime=mime,
        )
        return []

    with open(filepath) as f:
        text = f.read()

    return await associative_read(text, source=filepath)


async def _noop() -> list[list[float]]:
    return []

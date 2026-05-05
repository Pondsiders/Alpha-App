#!/usr/bin/env python3
"""Associative reading probe — does reading trigger memory?

Feed a text file through Qwen 3.5 4B for theme/query extraction,
then run the extracted queries through the recall pipeline to see
what memories surface. The goal: reading something should remind
me of things.

Usage:
    uv run python scripts/probe_associative_read.py /path/to/file.md
    uv run python scripts/probe_associative_read.py /path/to/file.md --max-tokens 2048
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

import httpx

# -- Config -------------------------------------------------------------------

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://primer:11434")
OLLAMA_CHAT_MODEL = "qwen3.5:4b"
OLLAMA_EMBED_MODEL = "nomic-embed-text"
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "4096"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")
MIN_IDF = 1.0  # Names with IDF below this are too common to search

# -- Extraction prompt --------------------------------------------------------

READING_EXTRACTION_PROMPT = """Alpha just read this text:

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

# -- Token counting -----------------------------------------------------------

_tokenizer = None


def get_tokenizer():
    global _tokenizer
    if _tokenizer is None:
        try:
            from tokenizers import Tokenizer
            _tokenizer = Tokenizer.from_pretrained("Qwen/Qwen3.5-4B")
        except Exception as e:
            print(f"  ⚠ Tokenizer not available ({e}), using char estimate")
            return None
    return _tokenizer


def count_tokens(text: str) -> int:
    tok = get_tokenizer()
    if tok:
        return len(tok.encode(text).ids)
    # Rough fallback: ~4 chars per token
    return len(text) // 4


# -- Ollama calls -------------------------------------------------------------

async def extract_queries(text: str) -> tuple[list[str], list[str], dict]:
    """Send text to Qwen for theme/query extraction. Returns (queries, names, stats)."""
    prompt = READING_EXTRACTION_PROMPT.format(text=text)

    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=60.0) as client:
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

    elapsed = time.monotonic() - t0
    result = response.json()
    output = result.get("message", {}).get("content", "")

    # Clean markdown fences if present
    cleaned = output.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    parsed = json.loads(cleaned)
    queries = [q for q in parsed.get("queries", []) if isinstance(q, str) and q.strip()]
    names = [n for n in parsed.get("names", []) if isinstance(n, str) and n.strip()]

    stats = {
        "elapsed_s": round(elapsed, 2),
        "input_tokens": result.get("prompt_eval_count", 0),
        "output_tokens": result.get("eval_count", 0),
    }

    return queries, names, stats


async def embed_queries_batch(queries: list[str]) -> list[list[float]]:
    """Batch-embed queries via Ollama."""
    if not queries:
        return []

    prefixed = [f"search_query: {q}" for q in queries]

    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{OLLAMA_URL}/api/embed",
            json={
                "model": OLLAMA_EMBED_MODEL,
                "input": prefixed,
                "keep_alive": -1,
            },
        )
        response.raise_for_status()

    elapsed = time.monotonic() - t0
    data = response.json()
    embeddings = data.get("embeddings", [])
    print(f"  Embedded {len(embeddings)} queries in {elapsed:.2f}s")
    return embeddings


# -- Database search ----------------------------------------------------------

async def search_by_embedding(embedding: list[float], limit: int = 3, min_score: float = 0.1):
    """Cosine similarity search against Cortex."""
    import asyncpg

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    id,
                    content,
                    metadata,
                    1 - (embedding <=> $1::vector) as score
                FROM cortex.memories
                WHERE NOT forgotten
                  AND embedding IS NOT NULL
                  AND 1 - (embedding <=> $1::vector) >= $2
                ORDER BY score DESC
                LIMIT $3
                """,
                json.dumps(embedding),
                min_score,
                limit,
            )
            return [
                {
                    "id": row["id"],
                    "content": row["content"],
                    "score": float(row["score"]),
                    "created_at": json.loads(row["metadata"]).get("created_at", ""),
                }
                for row in rows
            ]
    finally:
        await pool.close()


async def search_by_name(name: str, limit: int = 2):
    """Word-boundary name search against Cortex."""
    import asyncpg

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                r"""
                SELECT id, content, metadata
                FROM cortex.memories
                WHERE NOT forgotten
                  AND content ~* ('\m' || $1 || '\M')
                ORDER BY (metadata->>'created_at')::timestamptz DESC
                LIMIT $2
                """,
                name,
                limit,
            )
            return [
                {
                    "id": row["id"],
                    "content": row["content"],
                    "score": 1.0,  # Will be replaced with IDF
                    "created_at": json.loads(row["metadata"]).get("created_at", ""),
                }
                for row in rows
            ]
    finally:
        await pool.close()


async def count_memories_containing(name: str) -> int:
    """Count how many non-forgotten memories mention this name."""
    import asyncpg

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                r"""
                SELECT count(*)
                FROM cortex.memories
                WHERE NOT forgotten
                  AND content ~* ('\m' || $1 || '\M')
                """,
                name,
            )
    finally:
        await pool.close()


async def get_total_memory_count() -> int:
    """Total non-forgotten memories for IDF denominator."""
    import asyncpg

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT count(*) FROM cortex.memories WHERE NOT forgotten"
            )
    finally:
        await pool.close()


async def compute_idf(name: str, total: int) -> tuple[str, float, int]:
    """Compute IDF for a name. Returns (name, idf_score, doc_count)."""
    import math
    count = await count_memories_containing(name)
    if count == 0:
        return name, 0.0, 0
    idf = math.log(total / count)
    return name, idf, count


# -- Main pipeline ------------------------------------------------------------

async def associative_read(filepath: str, max_tokens: int | None = None):
    """The full pipeline: read → extract → search → display."""

    # Step 1: Read the file
    print(f"\n📖 Reading: {filepath}")
    with open(filepath) as f:
        text = f.read()

    token_count = count_tokens(text)
    word_count = len(text.split())
    print(f"   {word_count:,} words, ~{token_count:,} tokens")

    # Truncate if needed
    if max_tokens and token_count > max_tokens:
        # Rough truncation — cut by character ratio
        ratio = max_tokens / token_count
        text = text[:int(len(text) * ratio)]
        token_count = count_tokens(text)
        print(f"   Truncated to ~{token_count:,} tokens")

    if token_count > OLLAMA_NUM_CTX - 500:  # Leave room for the prompt wrapper
        print(f"   ⚠ Text ({token_count} tokens) may exceed num_ctx ({OLLAMA_NUM_CTX})")
        print(f"   Consider using --max-tokens {OLLAMA_NUM_CTX - 500}")

    # Step 2: Extract queries and names
    print(f"\n🧠 Extracting themes via {OLLAMA_CHAT_MODEL}...")
    queries, names, stats = await extract_queries(text)

    print(f"   Done in {stats['elapsed_s']}s ({stats['input_tokens']} in / {stats['output_tokens']} out)")
    print(f"\n   Queries ({len(queries)}):")
    for i, q in enumerate(queries, 1):
        print(f"     {i}. {q}")
    print(f"\n   Names ({len(names)}):")
    for n in names:
        print(f"     • {n}")

    if not queries and not names:
        print("\n   Nothing to search for. Done.")
        return

    # Step 3: Embed queries
    print(f"\n🔮 Embedding queries...")
    embeddings = await embed_queries_batch(queries)

    # Step 4: Search — cosine for queries, name search for names
    print(f"\n🔍 Searching Cortex...")

    all_memories = []
    seen_ids: set[int] = set()

    # Cosine search per query
    for i, (query, embedding) in enumerate(zip(queries, embeddings)):
        results = await search_by_embedding(embedding, limit=2)
        for mem in results:
            if mem["id"] not in seen_ids:
                mem["matched_by"] = f"query: {query}"
                all_memories.append(mem)
                seen_ids.add(mem["id"])

    # Name search with IDF filtering
    if names:
        total = await get_total_memory_count()
        print(f"   Total memories: {total:,}")
        idf_results = await asyncio.gather(*[compute_idf(n, total) for n in names])

        print(f"\n   IDF scores:")
        for name, idf, count in idf_results:
            status = "✓" if idf >= MIN_IDF else "✗ (too common)"
            if count == 0:
                status = "✗ (not found)"
            print(f"     {name}: IDF={idf:.2f} ({count} memories) {status}")

        informative = [(n, idf) for n, idf, count in idf_results if idf >= MIN_IDF]
        print(f"\n   {len(informative)}/{len(names)} names passed IDF filter")

        for name, idf in informative:
            results = await search_by_name(name, limit=2)
            for mem in results:
                if mem["id"] not in seen_ids:
                    mem["score"] = idf  # IDF as score
                    mem["matched_by"] = f"name: {name} (IDF={idf:.2f})"
                    all_memories.append(mem)
                    seen_ids.add(mem["id"])

    # Sort by score descending
    all_memories.sort(key=lambda m: m["score"], reverse=True)

    # Step 5: Display results
    print(f"\n{'='*72}")
    print(f"  ASSOCIATIONS ({len(all_memories)} memories surfaced)")
    print(f"{'='*72}")

    if not all_memories:
        print("\n  No memories resonated. The well is dry.")
        return

    for mem in all_memories:
        score = mem["score"]
        mem_id = mem["id"]
        matched = mem["matched_by"]
        content = mem["content"]

        # Truncate content for display
        if len(content) > 300:
            content = content[:300] + "..."

        print(f"\n  ── Memory #{mem_id} (score {score:.3f}) ──")
        print(f"  Matched by: {matched}")
        print(f"  {content}")

    print(f"\n{'='*72}")
    print(f"  Total: {len(all_memories)} unique memories from {len(queries)} queries + {len(names)} names")
    print(f"{'='*72}\n")


# -- CLI ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Associative reading probe")
    parser.add_argument("file", help="Path to text file to read")
    parser.add_argument("--max-tokens", type=int, default=None,
                        help="Truncate input to this many tokens")
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"File not found: {args.file}")
        sys.exit(1)

    if not DATABASE_URL:
        print("DATABASE_URL not set. Need it for Cortex search.")
        print("Try: DATABASE_URL=postgres://... uv run python scripts/probe_associative_read.py ...")
        sys.exit(1)

    asyncio.run(associative_read(args.file, args.max_tokens))


if __name__ == "__main__":
    main()

"""Associative recall — what sounds familiar from this message?

Unified pipeline: one function processes all content parts (text + images)
and returns recalled memories with optional image proxies.

Pipeline:
  foreach part in user_message:
    text → Qwen 3.5 4B → {"queries": [...], "names": [...]}
           queries → embed → cosine search (#1 per query, seen-cache filtered)
           names → IDF filter → FTS search (score = IDF, ranks above cosine)
    image → SHA256 hash → known?
            NEW: caption → embed → store (Garage + Cortex) → cosine search
            KNOWN: find earliest memory with this garage_key → cosine search

  merge all results, dedupe, sort by score descending
  for each hit with garage_key: fetch quarter-MP proxy from Garage
  return list[RecalledMemory]

The IDF IS the score for name hits. Names that appear rarely (high IDF)
rank above cosine hits. Names that appear everywhere (low IDF) get
filtered out entirely. Cosine hits fill in the vibes. One unified
ranking by informativeness.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import math
from typing import Any

import logfire
import pendulum
from openai import APIConnectionError, APIError, APITimeoutError

from alpha_app.constants import CHAT_MODEL
from alpha_app.inference_client import get_client

from .cortex import search_by_embedding, search_by_name, count_memories_containing
from .embeddings import embed_queries_batch, embed_document, embed_query, EmbeddingError

# -- Constants -----------------------------------------------------------------

_QUERY_LIMIT = 1     # One memory per association
_NAME_LIMIT = 1      # One memory per name
_MIN_COSINE = 0.1    # Minimum cosine similarity threshold
_MIN_IDF = 1.0       # Names with IDF below this are too common to search
_QUARTER_MP = 250_000  # ~512×512 pixels for recall thumbnails
_JPEG_QUALITY = 80

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

IMAGE_CAPTION_PROMPT = "Write a brief caption for this image in 2-3 sentences."

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


# -- Text extraction (chat completions) ---------------------------------------

_QUERY_NAME_SCHEMA = {
    "name": "recall_queries",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "queries": {"type": "array", "items": {"type": "string"}},
            "names": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["queries", "names"],
    },
}


async def _extract_queries_and_names(message: str) -> tuple[list[str], list[str]]:
    """Extract search queries AND proper names from a user message."""
    if not CHAT_MODEL:
        return [], []

    prompt = QUERY_EXTRACTION_PROMPT.format(message=message[:2000])
    messages = [{"role": "user", "content": prompt}]

    try:
        with logfire.span(
            "recall.extract_queries",
            **{
                "gen_ai.system": "openai",
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": CHAT_MODEL,
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
            # Qwen 3.5 non-thinking reasoning sampling (per model card).
            response = await get_client().chat.completions.create(
                model=CHAT_MODEL,
                messages=messages,
                temperature=1.0,
                top_p=0.95,
                presence_penalty=1.5,
                response_format={"type": "json_schema", "json_schema": _QUERY_NAME_SCHEMA},
                extra_body={
                    "top_k": 20,
                    "min_p": 0.0,
                    "repetition_penalty": 1.0,
                },
                timeout=15.0,
            )

            output = response.choices[0].message.content or ""

            if response.usage:
                span.set_attribute("gen_ai.usage.input_tokens", response.usage.prompt_tokens)
                span.set_attribute("gen_ai.usage.output_tokens", response.usage.completion_tokens)

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

    except (APITimeoutError, APIConnectionError, APIError, json.JSONDecodeError):
        return [], []
    except Exception:
        return [], []


# -- Image captioning ---------------------------------------------------------

async def _caption_image(image_b64: str) -> str:
    """Send image to Qwen 3.5 4B for captioning. Returns caption or ""."""
    if not CHAT_MODEL:
        return ""

    with logfire.span(
        "recall.caption_image",
        **{
            "gen_ai.system": "openai",
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": CHAT_MODEL,
        },
    ) as span:
        try:
            # Qwen 3.5 non-thinking general-task sampling (per model card).
            response = await get_client().chat.completions.create(
                model=CHAT_MODEL,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": IMAGE_CAPTION_PROMPT},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}",
                        }},
                    ],
                }],
                temperature=0.7,
                top_p=0.8,
                presence_penalty=1.5,
                extra_body={
                    "top_k": 20,
                    "min_p": 0.0,
                    "repetition_penalty": 1.0,
                },
                timeout=15.0,
            )
            caption = response.choices[0].message.content or ""

            if response.usage:
                span.set_attribute("gen_ai.usage.input_tokens", response.usage.prompt_tokens)
                span.set_attribute("gen_ai.usage.output_tokens", response.usage.completion_tokens)

            return caption
        except Exception as e:
            logfire.warn("recall.caption_image failed: {error}", error=str(e))
            return ""


# -- IDF computation -----------------------------------------------------------

async def _compute_idf(name: str, total_memories: int) -> tuple[str, float]:
    """Compute IDF for a name. Returns (name, idf_score)."""
    try:
        count = await count_memories_containing(name)
        if count == 0:
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
    queries: list[str],
    exclude: list[int],
) -> list[dict[str, Any]]:
    """Cosine similarity search per query. One memory per query, deduped."""
    if not embeddings:
        return []

    async def search_one(embedding: list[float], query: str) -> dict[str, Any] | None:
        results = await search_by_embedding(
            embedding=embedding,
            limit=_QUERY_LIMIT,
            exclude=exclude,
            min_score=_MIN_COSINE,
        )
        if results:
            mem = results[0]
            mem["trigger"] = query
            mem["trigger_type"] = "query"
            return mem
        return None

    tasks = [search_one(emb, q) for emb, q in zip(embeddings, queries)]
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

    total = await _get_total_memory_count()

    idf_tasks = [_compute_idf(name, total) for name in names]
    idf_results = await asyncio.gather(*idf_tasks)

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

    async def search_one(name: str, idf: float) -> dict[str, Any] | None:
        results = await search_by_name(
            name=name,
            limit=_NAME_LIMIT,
            exclude=exclude,
        )
        if results:
            mem = results[0]
            mem["score"] = idf
            mem["trigger"] = name
            mem["trigger_type"] = "name"
            return mem
        return None

    tasks = [search_one(name, idf) for name, idf in informative_names]
    results = await asyncio.gather(*tasks)

    memories = []
    seen_in_batch = set(exclude)
    for mem in results:
        if mem and mem["id"] not in seen_in_batch:
            memories.append(mem)
            seen_in_batch.add(mem["id"])

    return memories


# -- Image processing ----------------------------------------------------------

def _resize_to_1mp(image_data: bytes) -> bytes:
    """Resize image to ~1MP JPEG for Qwen captioning input."""
    from PIL import Image

    MAX_PIXELS = 1_000_000
    img = Image.open(io.BytesIO(image_data))
    w, h = img.size

    if w * h > MAX_PIXELS:
        scale = (MAX_PIXELS / (w * h)) ** 0.5
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    if img.mode != "RGB":
        img = img.convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _resize_for_recall(image_data: bytes) -> bytes:
    """Resize image to quarter-megapixel JPEG for recall injection.

    ~225 tokens per image at 512×512. Enough for recognition and
    association, not for reading fine text.
    """
    from PIL import Image

    img = Image.open(io.BytesIO(image_data))
    w, h = img.size

    if w * h > _QUARTER_MP:
        scale = (_QUARTER_MP / (w * h)) ** 0.5
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    if img.mode != "RGB":
        img = img.convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
    return buf.getvalue()


def _guess_content_type(data: bytes) -> str:
    """Guess content type from magic bytes."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:4] == b"GIF8":
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


async def _process_image_part(
    image_data: bytes,
    exclude: list[int],
    source: str = "attachment",
) -> list[dict[str, Any]]:
    """Process one image part: store if new, search either way.

    Returns memory dicts (same shape as text recall results).
    """
    from . import garage
    from .db import get_pool

    with logfire.span("recall.process_image") as span:
        # Hash for content addressing
        content_hash = hashlib.sha256(image_data).hexdigest()
        content_type = _guess_content_type(image_data)
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
               "image/webp": "webp"}.get(content_type, "bin")
        garage_key = f"images/{source}/{content_hash}.{ext}"

        # Known or new?
        is_known = await garage.head_object(garage_key)
        span.set_attribute("recall.image.is_known", is_known)
        span.set_attribute("recall.image.garage_key", garage_key)

        # Resize for Qwen captioning
        resized_jpeg = _resize_to_1mp(image_data)
        image_b64 = base64.b64encode(resized_jpeg).decode()

        # Store in Garage if new
        if not is_known:
            await garage.put_object(garage_key, image_data, content_type=content_type)

        # Caption via Qwen
        caption = await _caption_image(image_b64)
        if not caption:
            return []

        # Get the pool for DB operations
        pool = await get_pool()

        # Embed the caption and search BEFORE storing (prevents self-match)
        query_embedding = await embed_query(caption)
        if not query_embedding:
            return []

        results = await search_by_embedding(
            embedding=query_embedding,
            limit=_QUERY_LIMIT,
            exclude=exclude,
            min_score=_MIN_COSINE,
        )

        # Tag each result with the caption that found it
        for r in results:
            r["trigger"] = caption
            r["trigger_type"] = "image_caption"

        span.set_attribute("recall.image.search_results", len(results))

        # Store as new memory if this is the first time seeing this image
        if not is_known:
            doc_embedding = await embed_document(caption)
            if doc_embedding:
                from datetime import datetime, timezone
                created_at_utc = datetime.now(timezone.utc)
                vec_str = "[" + ",".join(str(x) for x in doc_embedding) + "]"
                metadata = {
                    "created_at": created_at_utc.isoformat(),
                    "garage_key": garage_key,
                    "source": source,
                    "content_hash": content_hash,
                    "type": "image",
                }
                try:
                    row = await pool.fetchrow(
                        """
                        INSERT INTO cortex.memories (content, embedding_qwen, metadata, created_at)
                        VALUES ($1, $2::vector, $3, $4)
                        RETURNING id
                        """,
                        caption,
                        vec_str,
                        json.dumps(metadata),
                        created_at_utc,
                    )
                    if row:
                        logfire.info(
                            "recall: stored new image memory #{id}",
                            id=row["id"],
                            garage_key=garage_key,
                        )
                except Exception as e:
                    logfire.warn("recall: store image memory failed: {error}", error=str(e))

        return results


# -- Image attachment (Garage fetch + resize) ----------------------------------

async def _attach_images(memories: list[dict[str, Any]]) -> None:
    """Fetch and resize images for memories that have garage_key.

    Modifies memories in place, adding 'image_b64' field.
    """
    from . import garage

    candidates = [m for m in memories if m.get("garage_key")]
    if not candidates:
        return

    with logfire.span(
        "recall.attach_images",
        memory_count=len(memories),
        image_candidates=len(candidates),
    ) as span:
        attached = 0
        failed = 0
        for mem in candidates:
            gk = mem["garage_key"]
            try:
                image_data = await garage.get_object(gk)
                if not image_data:
                    logfire.debug(
                        "recall.attach_images: garage returned None",
                        memory_id=mem["id"],
                        garage_key=gk,
                    )
                    failed += 1
                    continue
                resized = _resize_for_recall(image_data)
                mem["image_b64"] = base64.b64encode(resized).decode()
                attached += 1
                logfire.debug(
                    "recall.attach_images: attached",
                    memory_id=mem["id"],
                    original_bytes=len(image_data),
                    resized_bytes=len(resized),
                )
            except Exception as e:
                logfire.debug(
                    "recall.attach_images: failed",
                    memory_id=mem["id"],
                    error=str(e),
                )
                failed += 1

        span.set_attribute("recall.images.attached", attached)
        span.set_attribute("recall.images.failed", failed)


# -- Formatting ----------------------------------------------------------------

def _format_relative_time(created_at: str) -> str:
    """Format a memory's timestamp as relative time (PSO-8601)."""
    if not created_at:
        return "unknown"
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
    """Format a recall result as a ## Memory block.

    Format: ## Memory #{id} {PSO-8601 datetime} ({relative age}, score {score})
    Example: ## Memory #15034 Fri Mar 20 2026, 3:45 PM (1 week ago, score 0.89)
    """
    absolute = _format_absolute_datetime(mem["created_at"])
    relative = _format_relative_time(mem["created_at"])
    score = f"{mem['score']:.2f}"
    return f"## Memory #{mem['id']} {absolute} ({relative}, score {score})\n{mem['content']}"


def _format_absolute_datetime(created_at: str) -> str:
    """Format a memory's timestamp as PSO-8601 datetime."""
    if not created_at:
        return "unknown"
    try:
        dt = pendulum.parse(created_at).in_timezone("America/Los_Angeles")
        return dt.format("ddd MMM D YYYY, h:mm A")
    except Exception:
        return created_at


# -- Main entry point ----------------------------------------------------------

async def recall(
    parts: list[dict],
    *,
    session_id: str,
) -> list[dict[str, Any]]:
    """Unified recall: process all content parts, return enriched memories.

    The one recall function. Text parts go through Qwen extraction +
    cosine/IDF search. Image parts go through captioning + cosine search
    (storing new images along the way). All results merge, get images
    attached from Garage, and return sorted by score.

    Args:
        parts: User message content blocks (text and image parts).
        session_id: Chat ID for seen-cache scoping.

    Returns:
        List of memory dicts, each with:
          id, content, created_at, score, garage_key?, image_b64?
    """
    seen = get_seen_ids(session_id)
    seen_list = list(seen)

    with logfire.span("recall", session_id=session_id) as span:
        # Separate text and image parts
        text_parts = [
            p.get("text", "") for p in parts if p.get("type") == "text"
        ]
        image_parts = [
            p for p in parts
            if p.get("type") == "image" and isinstance(p.get("source"), dict)
            and p["source"].get("type") == "base64"
        ]

        user_text = " ".join(t for t in text_parts if t.strip())

        span.set_attribute("recall.text_parts", len(text_parts))
        span.set_attribute("recall.image_parts", len(image_parts))

        # -- Text recall -------------------------------------------------------
        text_memories: list[dict[str, Any]] = []

        if user_text.strip():
            queries, names = await _extract_queries_and_names(user_text)

            if queries or names:
                try:
                    embeddings, name_memories = await asyncio.gather(
                        embed_queries_batch(queries) if queries else _noop_embeddings(),
                        _search_by_names_with_idf(names, seen_list),
                    )
                except EmbeddingError:
                    embeddings = []
                    name_memories = await _search_by_names_with_idf(names, seen_list) if names else []

                name_ids = [m["id"] for m in name_memories]
                query_exclude = seen_list + name_ids
                query_memories = await _search_by_queries(embeddings, queries, query_exclude) if embeddings else []

                text_memories = name_memories + query_memories

        # -- Image recall ------------------------------------------------------
        image_memories: list[dict[str, Any]] = []

        for img_part in image_parts:
            try:
                raw_data = base64.b64decode(img_part["source"]["data"])
                # Exclude already-found IDs to avoid dupes across text + image
                all_found = seen_list + [m["id"] for m in text_memories] + [m["id"] for m in image_memories]
                results = await _process_image_part(raw_data, exclude=all_found)
                image_memories.extend(results)
            except Exception as e:
                logfire.warn("recall: image part failed: {error}", error=str(e))

        # -- Merge, dedupe, sort -----------------------------------------------
        all_memories = text_memories + image_memories

        # Dedupe by ID (shouldn't happen but belt-and-suspenders)
        seen_ids_in_batch: set[int] = set()
        deduped: list[dict[str, Any]] = []
        for mem in all_memories:
            if mem["id"] not in seen_ids_in_batch:
                deduped.append(mem)
                seen_ids_in_batch.add(mem["id"])
        all_memories = deduped

        all_memories.sort(key=lambda m: m.get("score", 0), reverse=True)

        # -- Attach images from Garage -----------------------------------------
        await _attach_images(all_memories)

        # -- Log each recalled memory for Logfire provenance --------------------
        for mem in all_memories:
            logfire.info(
                "recall.memory #{id} {score:.2f} {preview}",
                id=mem["id"],
                score=mem.get("score", 0),
                preview=mem.get("content", "")[:80],
                trigger_type=mem.get("trigger_type", "unknown"),
                trigger=mem.get("trigger", ""),
                has_image=bool(mem.get("image_b64")),
                has_garage_key=bool(mem.get("garage_key")),
                content=mem.get("content", ""),
                created_at=mem.get("created_at", ""),
            )

        # -- Mark seen ---------------------------------------------------------
        if all_memories:
            mark_seen(session_id, [m["id"] for m in all_memories])

        span.set_attribute("recall.total_results", len(all_memories))
        span.set_attribute("recall.text_results", len(text_memories))
        span.set_attribute("recall.image_results", len(image_memories))

    return all_memories


# Legacy entry points (keep for backward compat during transition)

async def recall_memories(text: str, *, session_id: str) -> list[str]:
    """Associative recall: return formatted memory blocks. DEPRECATED."""
    parts = [{"type": "text", "text": text}]
    memories = await recall(parts, session_id=session_id)
    return [format_memory(m) for m in memories]


async def recall_memories_rich(
    text: str, *, session_id: str,
) -> list[tuple[dict[str, Any], str]]:
    """Like recall_memories but returns (raw_dict, formatted_string). DEPRECATED."""
    parts = [{"type": "text", "text": text}]
    memories = await recall(parts, session_id=session_id)
    return [(m, format_memory(m)) for m in memories]


async def _noop_embeddings() -> list[list[float]]:
    """Return empty embeddings list — used when there are no queries."""
    return []

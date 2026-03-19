"""Vision pipeline — image understanding with visual memory.

The fusiform area of Alpha's brain. Every image, regardless of source
(user attachment, fetch, Forge, webcam), enters the same pipeline:

    hash → Garage check → resize → Garage store → Qwen caption → embed → fork

Fork:
  - NEW image: store caption as a Cortex memory (linked to Garage key)
  - KNOWN image: cosine search for matching memories (visual recall)

Returns recalled memories (same shape as text recall) for injection
into the UserMessage enrichment.
"""

from __future__ import annotations

import base64
import hashlib
import io
from typing import Any

import httpx
import logfire
from PIL import Image

from alpha_app.constants import OLLAMA_CHAT_MODEL, OLLAMA_NUM_CTX, OLLAMA_URL
from alpha_app.memories import garage
from alpha_app.memories.embeddings import embed_document as _embed_document, embed_query as _embed_query

# -- Config -------------------------------------------------------------------

MAX_PIXELS = 1_000_000  # 1 megapixel for Qwen input
JPEG_QUALITY = 85
CAPTION_PROMPT = "Write a brief caption for this image in 2-3 sentences."


# -- Public API ---------------------------------------------------------------


async def process_image(
    image_data: bytes,
    source: str = "attachment",
    *,
    db_pool: Any = None,
) -> list[dict]:
    """Run an image through the vision pipeline.

    Args:
        image_data: Raw image bytes (PNG, JPEG, etc.)
        source: Where the image came from ("attachment", "fetch", "forge", "webcam")
        db_pool: asyncpg connection pool for Cortex queries

    Returns:
        List of recalled memory dicts (same shape as text recall results):
        [{"id": int, "content": str, "score": float, "created_at": str}]
        Empty list if the image is new (a memory was stored instead).
    """
    with logfire.span("vision.process_image", source=source):
        # Step 1: Hash the raw bytes for content addressing
        content_hash = hashlib.sha256(image_data).hexdigest()
        content_type = _guess_content_type(image_data)
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
               "image/webp": "webp"}.get(content_type, "bin")
        garage_key = f"images/{source}/{content_hash}.{ext}"

        # Step 2: Check if we've seen this exact image before
        is_known = garage.head_object(garage_key)

        # Step 3: Resize for Qwen (60ms — cheap enough to derive every time)
        resized_jpeg = _resize_to_1mp(image_data)
        image_b64 = base64.b64encode(resized_jpeg).decode()

        # Step 4: Store original in Garage (one copy, derive 1MP on demand)
        if not is_known:
            garage.put_object(garage_key, image_data, content_type=content_type)

        # Step 5: Caption via Qwen 3.5 4B
        caption = await _caption_image(image_b64)
        if not caption:
            logfire.warn("vision: caption failed, skipping")
            return []

        # Step 6 + 7: Fork based on novelty.
        # NEW image: embed as DOCUMENT (search_document: prefix) → store in Cortex.
        # KNOWN image: embed as QUERY (search_query: prefix) → cosine search Cortex.
        # The prefix matters: nomic-embed-text produces different vectors for
        # documents vs queries, optimized for asymmetric retrieval.
        if not is_known and db_pool:
            doc_embedding = await _embed_document(caption)
            await _store_image_memory(
                db_pool, caption, doc_embedding, garage_key, source, content_hash,
            )
            return []
        elif db_pool:
            query_embedding = await _embed_query(caption)
            return await _search_memories(db_pool, query_embedding)
        else:
            return []


# -- Internal -----------------------------------------------------------------


def _resize_to_1mp(image_data: bytes) -> bytes:
    """Resize image to ~1MP JPEG for Qwen input."""
    img = Image.open(io.BytesIO(image_data))
    w, h = img.size

    if w * h > MAX_PIXELS:
        scale = (MAX_PIXELS / (w * h)) ** 0.5
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


async def _caption_image(image_b64: str) -> str:
    """Send image to Qwen 3.5 4B for captioning."""
    import json as _json

    # Logfire Model Run card expects messages in {role, parts: [{type, content}]} format
    input_messages = [{"role": "user", "parts": [
        {"type": "text", "content": CAPTION_PROMPT},
        {"type": "text", "content": "(image attached)"},
    ]}]
    output_placeholder = [{"role": "assistant", "parts": []}]

    with logfire.span(
        "vision.caption",
        **{
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "ollama",
            "gen_ai.provider.name": "ollama",
            "gen_ai.response.model": OLLAMA_CHAT_MODEL,
            "gen_ai.system_instructions": [{"type": "text", "content": CAPTION_PROMPT}],
            "gen_ai.input.messages": input_messages,
        },
    ) as span:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{OLLAMA_URL.rstrip('/')}/api/chat",
                    json={
                        "model": OLLAMA_CHAT_MODEL,
                        "messages": [{
                            "role": "user",
                            "content": CAPTION_PROMPT,
                            "images": [image_b64],
                        }],
                        "stream": False,
                        "options": {"num_ctx": OLLAMA_NUM_CTX, "temperature": 0},
                        "think": False,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                caption = data.get("message", {}).get("content", "")

                # Set gen_ai output attributes for Logfire Model Run card
                span.set_attribute(
                    "gen_ai.output.messages",
                    [{"role": "assistant", "parts": [{"type": "text", "content": caption}]}],
                )
                tokens = data.get("eval_count", 0)
                if tokens:
                    span.set_attribute("gen_ai.usage.output_tokens", tokens)
                prompt_tokens = data.get("prompt_eval_count", 0)
                if prompt_tokens:
                    span.set_attribute("gen_ai.usage.input_tokens", prompt_tokens)

                return caption
        except Exception as e:
            logfire.warn("vision.caption failed: {error}", error=str(e))
            return ""


async def _store_image_memory(
    pool: Any,
    caption: str,
    embedding: list[float],
    garage_key: str,
    source: str,
    content_hash: str,
) -> int | None:
    """Store a new image memory in Cortex."""
    import json

    with logfire.span("vision.store_memory"):
        vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
        metadata = json.dumps({
            "garage_key": garage_key,
            "source": source,
            "content_hash": content_hash,
            "type": "image",
        })

        try:
            row = await pool.fetchrow(
                """
                INSERT INTO cortex.memories (content, embedding, metadata)
                VALUES ($1, $2::vector, $3::jsonb)
                RETURNING id
                """,
                caption,
                vec_str,
                metadata,
            )
            memory_id = row["id"] if row else None
            if memory_id:
                logfire.info(
                    "vision: stored image memory #{id}",
                    id=memory_id,
                )
            return memory_id
        except Exception as e:
            logfire.warn("vision.store_memory failed: {error}", error=str(e))
            return None


async def _search_memories(
    pool: Any,
    embedding: list[float],
    top_k: int = 5,
) -> list[dict]:
    """Cosine search Cortex for memories related to this image."""
    with logfire.span("vision.search_memories") as span:
        vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
        try:
            rows = await pool.fetch(
                """
                SELECT id, content,
                       1 - (embedding <=> $1::vector) AS score,
                       metadata->>'created_at' AS created_at
                FROM cortex.memories
                WHERE NOT forgotten
                ORDER BY embedding <=> $1::vector
                LIMIT $2
                """,
                vec_str,
                top_k,
            )
            results = [
                {
                    "id": row["id"],
                    "content": row["content"],
                    "score": float(row["score"]),
                    "created_at": row["created_at"] or "unknown",
                }
                for row in rows
                if float(row["score"]) > 0.5  # minimum similarity threshold
            ]

            # Attach results to span for Logfire visibility
            span.set_attribute("vision.search.result_count", len(results))
            for i, mem in enumerate(results[:5]):
                span.set_attribute(f"vision.search.result.{i}.id", mem["id"])
                span.set_attribute(f"vision.search.result.{i}.score", round(mem["score"], 4))
                span.set_attribute(f"vision.search.result.{i}.preview", mem["content"][:100])

            return results
        except Exception as e:
            logfire.warn("vision.search_memories failed: {error}", error=str(e))
            return []


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

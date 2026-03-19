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
from alpha_app.memories.embeddings import embed_document, embed_query

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
        garage_key = f"images/{source}/{content_hash}.jpg"

        # Step 2: Check if we've seen this exact image before
        is_known = garage.head_object(garage_key)

        # Step 3: Resize for Qwen (regardless of novelty — we need the caption)
        resized_jpeg = _resize_to_1mp(image_data)
        image_b64 = base64.b64encode(resized_jpeg).decode()

        # Step 4: Store in Garage (skip if already there)
        if not is_known:
            # Store the original (full-res) for archival
            garage.put_object(
                f"images/{source}/{content_hash}_original",
                image_data,
                content_type=_guess_content_type(image_data),
            )
            # Store the 1MP JPEG for quick retrieval
            garage.put_object(garage_key, resized_jpeg, content_type="image/jpeg")

        # Step 5: Caption via Qwen 3.5 4B
        caption = await _caption_image(image_b64)
        if not caption:
            logfire.warn("vision: caption failed, skipping")
            return []

        # Step 6: Embed the caption
        embedding = await embed_query(caption)

        # Step 7: Fork based on novelty
        if not is_known and db_pool:
            # NEW image — store a memory
            await _store_image_memory(
                db_pool, caption, embedding, garage_key, source, content_hash,
            )
            return []
        elif db_pool:
            # KNOWN image — recall related memories
            return await _search_memories(db_pool, embedding)
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
    with logfire.span("vision.caption", model=OLLAMA_CHAT_MODEL):
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
                return resp.json().get("message", {}).get("content", "")
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
    with logfire.span("vision.search_memories"):
        vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
        try:
            rows = await pool.fetch(
                """
                SELECT id, content,
                       1 - (embedding <=> $1::vector) AS score,
                       created_at
                FROM cortex.memories
                ORDER BY embedding <=> $1::vector
                LIMIT $2
                """,
                vec_str,
                top_k,
            )
            return [
                {
                    "id": row["id"],
                    "content": row["content"],
                    "score": float(row["score"]),
                    "created_at": str(row["created_at"]),
                }
                for row in rows
                if float(row["score"]) > 0.5  # minimum similarity threshold
            ]
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

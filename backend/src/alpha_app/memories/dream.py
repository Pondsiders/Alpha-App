"""Dream — image generation via Runpod + vision pipeline.

One function: dream(prompt) → generates an image, processes it through
the vision pipeline (Garage store, Qwen caption, Cortex memory), and
returns the image as base64 alongside any recalled memories.

The full loop: prompt → generate → see → store → remember.
"""

from __future__ import annotations

import base64
import os
from typing import Any

import httpx
import logfire

from alpha_app.memories.vision import process_image

# Runpod config — endpoint and API key from environment
RUNPOD_API_KEY = os.environ.get("RUNPOD_API_KEY", "")
RUNPOD_ENDPOINT_ID = os.environ.get("RUNPOD_ENDPOINT_ID", "")
RUNPOD_URL = f"https://api.runpod.ai/v2/{RUNPOD_ENDPOINT_ID}/runsync"


async def dream(
    prompt: str,
    *,
    negative_prompt: str = "blurry, low quality, deformed, ugly, text, watermark, signature",
    width: int = 1152,
    height: int = 768,
    db_pool: Any = None,
) -> dict:
    """Generate an image and process it through the vision pipeline.

    Args:
        prompt: The image generation prompt.
        negative_prompt: Things to avoid in the generation.
        width: Image width (default 1152 for 3:2 landscape).
        height: Image height (default 768).
        db_pool: asyncpg pool for Cortex queries.

    Returns:
        {
            "image": base64 PNG string (for tool result content block),
            "prompt": the prompt used,
            "generation_time": seconds,
            "memories": list of recalled memories (from vision pipeline),
            "memory_stored": bool (True if this is a new image),
        }
    """
    if not RUNPOD_API_KEY or not RUNPOD_ENDPOINT_ID:
        return {"error": "RUNPOD_API_KEY or RUNPOD_ENDPOINT_ID not set"}

    with logfire.span(
        "dream.generate",
        prompt=prompt,
        width=width,
        height=height,
    ) as span:
        # Step 1: Generate via Runpod
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    RUNPOD_URL,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {RUNPOD_API_KEY}",
                    },
                    json={
                        "input": {
                            "prompt": prompt,
                            "negative_prompt": negative_prompt,
                            "width": width,
                            "height": height,
                            "num_inference_steps": 1,
                            "guidance_scale": 0,
                            "seed": -1,
                            "num_images": 1,
                        }
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logfire.warn("dream.generate failed: {error}", error=str(e))
            return {"error": f"Generation failed: {e}"}

        if data.get("status") != "COMPLETED":
            error = data.get("error", "Unknown error")
            return {"error": f"Runpod error: {error}"}

        # Extract image
        output = data.get("output", {})
        images = output.get("images", [])
        if not images:
            return {"error": "No images in Runpod response"}

        image_b64 = images[0].get("image", "")
        if not image_b64:
            return {"error": "Empty image data"}

        gen_time = output.get("generation_time", 0)
        span.set_attribute("dream.generation_time", gen_time)

        # Step 2: Decode and run through vision pipeline
        image_bytes = base64.b64decode(image_b64)

        memories = []
        if db_pool:
            memories = await process_image(
                image_bytes,
                source="dream",
                db_pool=db_pool,
            )

        return {
            "image": image_b64,
            "prompt": prompt,
            "generation_time": gen_time,
            "memories": memories,
            "memory_stored": len(memories) == 0,  # no recalled = new = stored
        }

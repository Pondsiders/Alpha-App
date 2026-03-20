"""Cortex memory tools — FastMCP server for alpha_app.

Direct Postgres access — no HTTP layer, no Cortex service dependency.
The store() tool optionally clears the pending memorables buffer,
closing the feedback loop with Intro.

Usage:
    from alpha_app.tools.cortex import create_cortex_server

    server = create_cortex_server()
    # Pass to Claude(mcp_servers={"cortex": server})
"""

from __future__ import annotations

from typing import Callable, TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from ..db import get_pool
from ..memories.cortex import (
    store as cortex_store,
    search as cortex_search,
    recent as cortex_recent,
    get as cortex_get,
)
from ..memories.dream import dream as dream_generate
from ..memories.recall import mark_seen

if TYPE_CHECKING:
    from ..topics import TopicRegistry


def create_cortex_server(
    clear_memorables: Callable[[], int] | None = None,
    topic_registry: TopicRegistry | None = None,
    session_id: str | None = None,
) -> FastMCP:
    """Create the Cortex MCP server.

    Args:
        clear_memorables: Optional callable that clears pending memorables and
                         returns the count cleared. Provided by the consumer
                         when the suggest pipeline is wired up.

    Returns:
        FastMCP server instance ready for dispatch
    """

    server = FastMCP("cortex")

    @server.tool(
        description=(
            "Store a memory in Cortex. Use this to remember important moments, "
            "realizations, or anything worth preserving."
        ),
    )
    async def store(memory: str, image: str | None = None) -> str:
        """Store a memory and optionally clear the memorables buffer."""
        result = await cortex_store(memory, image=image)

        if result is None:
            return "Error storing memory"

        memory_id = result.get("id", "unknown")

        # Mark as seen so recall won't resurface this memory in the same session.
        # Prevents the self-referencing problem: storing a memory about X
        # and then recalling it when X is mentioned again.
        if session_id and isinstance(memory_id, int):
            mark_seen(session_id, [memory_id])

        # Clear the memorables buffer — feedback mechanism with Intro
        cleared = clear_memorables() if clear_memorables else 0

        # Build response
        response = f"Memory stored (id: {memory_id})"
        if result.get("thumbnail_path"):
            response += f" [image: {result['thumbnail_path']}]"
        if cleared > 0:
            response += f" - cleared {cleared} pending suggestion(s)"

        return response

    @server.tool(
        description=(
            "Search memories in Cortex. Returns semantically similar memories. "
            "Limit defaults to 5."
        ),
    )
    async def search(query: str) -> str:
        """Search for memories matching a query."""
        memories = await cortex_search(query, limit=5)

        if not memories:
            return "No memories found."

        # Format results
        lines = [f"Found {len(memories)} memor{'y' if len(memories) == 1 else 'ies'}:\n"]
        for mem in memories:
            score = mem.get("score", 0)
            content = mem.get("content", "")
            created = mem.get("created_at", "")[:10]  # Just the date
            image_flag = " [img]" if mem.get("image_path") else ""
            lines.append(f"[{score:.2f}] ({created}{image_flag}) {content}\n")

        return "\n".join(lines)

    @server.tool(
        description="Get recent memories from Cortex. Limit defaults to 10.",
    )
    async def recent() -> str:
        """Get the most recent memories."""
        memories = await cortex_recent(limit=10)

        if not memories:
            return "No recent memories."

        # Format results
        lines = [f"Last {len(memories)} memor{'y' if len(memories) == 1 else 'ies'}:\n"]
        for mem in memories:
            content = mem.get("content", "")
            created = mem.get("created_at", "")[:16]  # Date and time
            image_flag = " [img]" if mem.get("image_path") else ""
            lines.append(f"({created}{image_flag}) {content}\n")

        return "\n".join(lines)

    @server.tool(
        description="Get a specific memory by its ID.",
    )
    async def get(memory_id: int) -> str:
        """Retrieve a single memory by ID."""
        mem = await cortex_get(memory_id)

        if mem is None:
            return f"Memory {memory_id} not found."

        content = mem.get("content", "")
        created = mem.get("created_at", "")
        tags = mem.get("tags")
        image_flag = f"\n[image: {mem['image_path']}]" if mem.get("image_path") else ""

        result = f"Memory {memory_id} ({created}):\n{content}"
        if tags:
            result += f"\nTags: {', '.join(tags)}"
        result += image_flag

        return result

    # -- Dream tool (image generation via Runpod + vision pipeline) --

    @server.tool(
        description=(
            "Generate an image from a text prompt. The image is created by SDXL "
            "on Runpod, then processed through the vision pipeline: stored in "
            "Garage, captioned by Qwen, embedded, and either stored as a new "
            "memory or matched against existing memories. Returns the image "
            "as a viewable content block."
        ),
    )
    async def imagine(
        prompt: str,
        negative_prompt: str = "blurry, low quality, deformed, ugly, text, watermark, signature",
        width: int = 1152,
        height: int = 768,
    ) -> list:
        """Generate an image and process it through the vision pipeline."""
        result = await dream_generate(
            prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            db_pool=get_pool(),
        )

        if "error" in result:
            return [{"type": "text", "text": f"Dream failed: {result['error']}"}]

        # Build response: image content block + text summary
        parts = []

        # The image as a viewable content block
        parts.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": result["image"],
            },
        })

        # Text summary
        summary = f'Dream: "{prompt}" ({result["generation_time"]:.1f}s)'
        if result.get("memory_stored"):
            summary += " — stored as new memory"
        if result.get("memories"):
            memory_ids = [str(m["id"]) for m in result["memories"]]
            summary += f" — recalled: #{', #'.join(memory_ids)}"
        parts.append({"type": "text", "text": summary})

        return parts

    # -- Topic context tool --
    if topic_registry is not None:
        _registry = topic_registry  # close over for the tool

        @server.tool(
            description=(
                "Get topic context. Load architecture docs, current state, "
                "and relevant details for a topic. "
                "Call list_topics first to see what's available."
            ),
        )
        def topic_context(topic: str) -> str:
            """Load context for a topic."""
            context = _registry.get_context(topic)
            if context is None:
                available = ", ".join(_registry.list_topics())
                return f"Unknown topic: '{topic}'. Available: {available}"
            return context

        @server.tool(
            description="List available project topics for context loading.",
        )
        def list_topics() -> str:
            """List all available topics."""
            topics = _registry.list_topics()
            if not topics:
                return "No topics available."
            return "Available topics: " + ", ".join(topics)

    return server

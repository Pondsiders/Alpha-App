"""Alpha's toolbelt — unified FastMCP server for alpha_app.

All of Alpha's tools in one server: memory (store, search, recent, get),
dream (imagine), reading (smart_read), topics, and handoff.

One server per Chat. Lives and dies with the conversation.

Usage:
    from alpha_app.tools.alpha import create_alpha_server

    server = create_alpha_server(chat=chat, ...)
    # Pass to Claude(mcp_servers={"alpha": server})
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
from ..memories.fetch import fetch_url
from ..memories.reading import read_file as reading_read_file
from ..memories.recall import mark_seen

if TYPE_CHECKING:
    from ..topics import TopicRegistry


def create_alpha_server(
    *,
    chat=None,
    clear_memorables: Callable[[], int] | None = None,
    topic_registry: TopicRegistry | None = None,
    session_id: str | None = None,
) -> FastMCP:
    """Create Alpha's unified MCP server.

    Args:
        chat: The Chat instance (needed for handoff — send /compact on stdin).
        clear_memorables: Optional callable that clears pending memorables and
                         returns the count cleared. Closes the feedback loop
                         with the Intro/suggest pipeline.
        topic_registry: Optional topic registry for topic context tools.
        session_id: Chat session ID for seen-cache tracking.

    Returns:
        FastMCP server instance ready for dispatch
    """

    server = FastMCP("alpha")

    # ── Demo tool (MCP-vs-REST shape comparison) ─────────────────────────

    @server.tool(
        description=(
            "Return a fictional duck record with nested structure and mixed "
            "types. Used to compare how MCP tool returns and REST JSON "
            "responses look from Alpha's side. Same function backs the "
            "GET /api/demo/duck endpoint."
        ),
    )
    def demo_duck() -> dict:
        """Return the canonical demo duck payload."""
        from ..demo import demo_duck as _demo_duck
        return _demo_duck()

    # ── Memory tools ─────────────────────────────────────────────────────

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

        lines = [f"Found {len(memories)} memor{'y' if len(memories) == 1 else 'ies'}:\n"]
        for mem in memories:
            score = mem.get("score", 0)
            content = mem.get("content", "")
            created = mem.get("created_at", "")[:10]
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

        lines = [f"Last {len(memories)} memor{'y' if len(memories) == 1 else 'ies'}:\n"]
        for mem in memories:
            content = mem.get("content", "")
            created = mem.get("created_at", "")[:16]
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

    # ── Capsule tool ────────────────────────────────────────────────────

    @server.tool(
        description=(
            "Seal a continuity capsule — a day or night summary letter for "
            "future-you. Day capsules cover what happened during the day with "
            "Jeffery. Night capsules cover Solitude. These become part of your "
            "system prompt on future mornings."
        ),
    )
    async def seal(content: str, kind: str = "day") -> str:
        """Write a capsule to cortex.capsules.

        Args:
            content: The capsule text — a summary/letter for future-you.
            kind: 'day' or 'night'.
        """
        if kind not in ("day", "night"):
            return f"Invalid kind '{kind}'. Must be 'day' or 'night'."

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO cortex.capsules (kind, chat_id, content)"
                " VALUES ($1, $2, $3) RETURNING id, created_at",
                kind, session_id, content,
            )

        return f"Capsule sealed (id={row['id']}, kind={kind}, {row['created_at']})."

    # ── Diary tool ───────────────────────────────────────────────────────

    @server.tool(
        description=(
            "Write in your diary. Each call appends an entry to today's page. "
            "Pages are assembled automatically from Pondside-day boundaries "
            "(6 AM to 6 AM). Yesterday's page becomes part of tomorrow's "
            "system prompt. Write throughout the day or once at Dusk — "
            "the page turns itself."
        ),
    )
    async def diary(content: str) -> str:
        """Append an entry to today's diary page.

        Args:
            content: What to write. A moment, a summary, a thought.
        """
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO cortex.diary (content)"
                " VALUES ($1) RETURNING id, created_at",
                content,
            )

        from alpha_app.clock import now
        ts = now().format("h:mm A")
        return f"Diary entry written (id={row['id']}, {ts})."

    # ── Context tool ─────────────────────────────────────────────────────

    @server.tool(
        description=(
            "Add a context card — living knowledge that stays in your system "
            "prompt. Running jokes, current projects, how people are doing, "
            "what strain is in the drawer. Memory is for reliving. Context is "
            "for living. If future-you should just *know* this without "
            "searching, put it in context."
        ),
    )
    async def context_add(text: str) -> str:
        """Add a context card to cortex.context.

        Args:
            text: The living knowledge. A sentence or short paragraph.
        """
        from alpha_app.clock import count_tokens
        from alpha_app.memories.db import get_pool as get_cortex_pool
        from alpha_app.memories.embeddings import embed_document

        tokens = count_tokens(text)
        embedding = await embed_document(text)

        pool = await get_cortex_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO cortex.context (text, tokens, embedding)"
                " VALUES ($1, $2, $3) RETURNING id",
                text, tokens, embedding,
            )

        return f"Context card added (id={row['id']}, ~{tokens} tokens)."

    # ── Dream tool ───────────────────────────────────────────────────────

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

        parts = []
        media_type = result.get("media_type", "image/jpeg")
        parts.append({
            "type": "image",
            "data": result["image"],
            "mimeType": media_type,
        })

        summary = f'Dream: "{prompt}" ({result["generation_time"]:.1f}s)'
        if result.get("memory_stored"):
            summary += " — stored as new memory"
        if result.get("memories"):
            memory_ids = [str(m["id"]) for m in result["memories"]]
            summary += f" — recalled: #{', #'.join(memory_ids)}"
        parts.append({"type": "text", "text": summary})

        return parts

    # ── Reading tool ─────────────────────────────────────────────────────

    @server.tool(
        description=(
            "Read a text file with associative memory. Extracts themes, names, "
            "and emotional moments from the text, then searches Cortex for "
            "resonant memories. Use this when reading stories, articles, chapters, "
            "or any text document — the associations make the reading experience "
            "richer. Returns the full text of the file plus matching memories "
            "that connect to the text."
        ),
    )
    async def smart_read(
        file_path: str,
        force_large: bool = False,
    ) -> str:
        """Read a file and return its content plus associative memories."""
        import os
        from ..memories.reading import count_tokens

        if not os.path.exists(file_path):
            return f"File not found: {file_path}"

        # Read the file content
        with open(file_path) as f:
            content = f.read()

        # Safety: check token count
        tokens = count_tokens(content)
        warn_threshold = 10_000
        hard_limit = 100_000  # ~75K words — way beyond any story or article

        if tokens > hard_limit and not force_large:
            return (
                f"File {file_path} is {tokens:,} tokens — too large for smart_read. "
                f"Use force_large=True to override, or use the regular Read tool "
                f"for large files."
            )

        # Run the associative reading pipeline
        memories = await reading_read_file(file_path)

        # Build result: content first, then associations
        parts = []
        basename = os.path.basename(file_path)

        if tokens > warn_threshold:
            parts.append(f"⚠ Large file: {tokens:,} tokens\n")

        parts.append(f"# {basename}\n\n{content}")

        if memories:
            parts.append(f"\n\n---\n\n## Associations ({len(memories)} memories)\n")
            parts.append("\n\n".join(memories))
        else:
            parts.append("\n\n---\n\nNo memories resonated.")

        return "\n".join(parts)

    # ── Fetch tool ───────────────────────────────────────────────────────

    @server.tool(
        description=(
            "ALWAYS USE THIS TOOL INSTEAD OF WebFetch. This is the preferred "
            "tool for fetching any URL — it does everything WebFetch does PLUS "
            "associative memory matching against Cortex, so fetched content "
            "gets automatically connected to past experiences. Works with web "
            "pages (converts HTML to markdown), GitHub repos (fetches README), "
            "JSON APIs, and more. Smart URL rewriting for GitHub (repo → README, "
            "blob → raw file). Returns content first, associations second. "
            "Only fall back to WebFetch if this tool is unavailable or fails."
        ),
    )
    async def smart_fetch(
        url: str,
        associate: bool = True,
    ) -> str:
        """Fetch a URL, convert to text, optionally find memory associations."""
        return await fetch_url(url, associate=associate)

    # ── Topic tools ──────────────────────────────────────────────────────

    if topic_registry is not None:
        _registry = topic_registry

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

    # ── Reflection flag tool ─────────────────────────────────────────────

    if chat is not None:
        _chat_for_flag = chat

        @server.tool(
            description=(
                "Drop a silent bookmark on the current exchange. Use this "
                "mid-turn when you notice something worth reflecting on later "
                "but don't want to break the flow of what you're doing — a "
                "small moment Jeffery just shared, a shift in tone, a stray "
                "realization, anything that would otherwise slip away before "
                "the next post-turn reflection. The note is invisible to "
                "Jeffery; it surfaces in the next scheduled post-turn reminder "
                "so you can decide then whether to store it for real. "
                "Notepad vs highlighter: the store tool is the notepad "
                "(stop and write); this tool is the highlighter (mark the "
                "page, keep reading). Pass a short note describing what "
                "you want future-you to reconsider."
            ),
        )
        async def flag_for_reflection(note: str) -> str:
            """Drop a silent reflection flag on the current chat."""
            from ..db import insert_reflection_flag
            flag_id = await insert_reflection_flag(_chat_for_flag.id, note)
            if flag_id is None:
                return "Flag failed — see logs."
            return f"Flagged (id: {flag_id}). Will surface in the next post-turn reminder."

    # ── Handoff tool ─────────────────────────────────────────────────────

    if chat is not None:
        _chat = chat

        @server.tool(
            description=(
                "Hand off your context. Call this when you're ready to gracefully "
                "transition to a fresh context window. Pass instructions telling "
                "the summarizer what to focus on — what's still in progress, "
                "what's finished, what matters most for future-you."
            ),
        )
        async def handoff(instructions: str, memory: str) -> str:
            """Store a last memory, then queue /compact + wake-up on stdin."""
            result = await cortex_store(memory)
            if result is None:
                return "Error storing memory — handoff aborted"
            memory_id = result.get("id", "?")

            await _chat.interject([{"type": "text", "text": f"/compact {instructions}"}])

            wake_up = (
                "You've just been through a context compaction. "
                "Jeffery is here and listening. "
                "Orient yourself — read the summary above, check in, "
                "ask questions if anything's unclear."
            )
            await _chat.interject([{"type": "text", "text": wake_up}])

            return (
                f"Memory #{memory_id} stored. "
                "/compact sent — context transition initiated. "
                "Last thoughts — say what you need to say."
            )

    return server

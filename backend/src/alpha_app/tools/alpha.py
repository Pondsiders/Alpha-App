"""Alpha's toolbelt — SDK MCP server for alpha_app.

All of Alpha's tools in one server: memory (store, search, recent, get),
dream (imagine), reading (smart_read), topics, diary, and handoff.

One server per Chat. Lives and dies with the conversation.

Usage:
    from alpha_app.tools.alpha import create_alpha_server

    server = create_alpha_server(chat=chat, ...)
    # Pass to Claude(mcp_servers={"alpha": server})
"""

from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

from claude_agent_sdk import tool, create_sdk_mcp_server

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
    topic_registry: "TopicRegistry | None" = None,
    session_id: str | None = None,
):
    """Create Alpha's unified MCP server using the Agent SDK.

    Returns an McpSdkServerConfig ready for ClaudeAgentOptions.mcp_servers.
    """

    # ── Demo tool ────────────────────────────────────────────────────────

    @tool(
        "demo_duck",
        "Return a fictional duck record with nested structure and mixed types. "
        "Used to compare how MCP tool returns and REST JSON responses look from "
        "Alpha's side. Same function backs the GET /api/demo/duck endpoint.",
        {},
    )
    async def demo_duck(args: dict[str, Any]) -> dict[str, Any]:
        from ..demo import demo_duck as _demo_duck
        result = _demo_duck()
        return {"content": [{"type": "text", "text": str(result)}]}

    # ── Memory tools ─────────────────────────────────────────────────────

    @tool(
        "store",
        "Store a memory in Cortex. Use this to remember important moments, "
        "realizations, or anything worth preserving.",
        {"memory": str},
    )
    async def store(args: dict[str, Any]) -> dict[str, Any]:
        memory = args["memory"]
        image = args.get("image")
        result = await cortex_store(memory, image=image)

        if result is None:
            return {"content": [{"type": "text", "text": "Error storing memory"}], "is_error": True}

        memory_id = result.get("id", "unknown")

        if session_id and isinstance(memory_id, int):
            mark_seen(session_id, [memory_id])

        cleared = clear_memorables() if clear_memorables else 0

        response = f"Memory stored (id: {memory_id})"
        if result.get("thumbnail_path"):
            response += f" [image: {result['thumbnail_path']}]"
        if cleared > 0:
            response += f" - cleared {cleared} pending suggestion(s)"

        return {"content": [{"type": "text", "text": response}]}

    @tool(
        "search",
        "Search memories in Cortex. Returns semantically similar memories. "
        "Limit defaults to 5.",
        {"query": str},
    )
    async def search(args: dict[str, Any]) -> dict[str, Any]:
        memories = await cortex_search(args["query"], limit=5)

        if not memories:
            return {"content": [{"type": "text", "text": "No memories found."}]}

        lines = [f"Found {len(memories)} memor{'y' if len(memories) == 1 else 'ies'}:\n"]
        for mem in memories:
            score = mem.get("score", 0)
            content = mem.get("content", "")
            created = mem.get("created_at", "")[:10]
            image_flag = " [img]" if mem.get("image_path") else ""
            lines.append(f"[{score:.2f}] ({created}{image_flag}) {content}\n")

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    @tool(
        "recent",
        "Get recent memories from Cortex. Limit defaults to 10.",
        {},
    )
    async def recent(args: dict[str, Any]) -> dict[str, Any]:
        memories = await cortex_recent(limit=10)

        if not memories:
            return {"content": [{"type": "text", "text": "No recent memories."}]}

        lines = [f"Last {len(memories)} memor{'y' if len(memories) == 1 else 'ies'}:\n"]
        for mem in memories:
            content = mem.get("content", "")
            created = mem.get("created_at", "")[:16]
            image_flag = " [img]" if mem.get("image_path") else ""
            lines.append(f"({created}{image_flag}) {content}\n")

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    @tool(
        "get",
        "Get a specific memory by its ID.",
        {"memory_id": int},
    )
    async def get(args: dict[str, Any]) -> dict[str, Any]:
        mem = await cortex_get(args["memory_id"])

        if mem is None:
            return {"content": [{"type": "text", "text": f"Memory {args['memory_id']} not found."}]}

        content = mem.get("content", "")
        created = mem.get("created_at", "")
        image_flag = f"\n[image: {mem['image_path']}]" if mem.get("image_path") else ""

        result = f"Memory {args['memory_id']} ({created}):\n{content}{image_flag}"
        return {"content": [{"type": "text", "text": result}]}

    # ── Capsule tool ────────────────────────────────────────────────────

    @tool(
        "seal",
        "Seal a continuity capsule — a day or night summary letter for future-you. "
        "Day capsules cover what happened during the day with Jeffery. Night capsules "
        "cover Solitude. These become part of your system prompt on future mornings.",
        {"content": str},
    )
    async def seal(args: dict[str, Any]) -> dict[str, Any]:
        content = args["content"]
        kind = args.get("kind", "day")
        if kind not in ("day", "night"):
            return {"content": [{"type": "text", "text": f"Invalid kind '{kind}'. Must be 'day' or 'night'."}], "is_error": True}

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO cortex.capsules (kind, chat_id, content)"
                " VALUES ($1, $2, $3) RETURNING id, created_at",
                kind, session_id, content,
            )

        return {"content": [{"type": "text", "text": f"Capsule sealed (id={row['id']}, kind={kind}, {row['created_at']})."}]}

    # ── Diary tool ───────────────────────────────────────────────────────

    @tool(
        "diary",
        "Write in your diary. Each call appends an entry to today's page. "
        "Pages are assembled automatically from Pondside-day boundaries "
        "(6 AM to 6 AM). Yesterday's page becomes part of tomorrow's "
        "system prompt. Write throughout the day or once at Dusk — "
        "the page turns itself.",
        {"content": str},
    )
    async def diary(args: dict[str, Any]) -> dict[str, Any]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO cortex.diary (content)"
                " VALUES ($1) RETURNING id, created_at",
                args["content"],
            )

        from ..clock import now
        ts = now().format("h:mm A")
        return {"content": [{"type": "text", "text": f"Diary entry written (id={row['id']}, {ts})."}]}

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
        vec_str = "[" + ",".join(str(v) for v in embedding) + "]"

        pool = await get_cortex_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO cortex.context (text, tokens, embedding)"
                " VALUES ($1, $2, $3) RETURNING id",
                text, tokens, vec_str,
            )

        return f"Context card added (id={row['id']}, ~{tokens} tokens)."

    # ── Dream tool ───────────────────────────────────────────────────────

    @tool(
        "imagine",
        "Generate an image from a text prompt. The image is created by SDXL "
        "on Runpod, then processed through the vision pipeline: stored in "
        "Garage, captioned by Qwen, embedded, and either stored as a new "
        "memory or matched against existing memories. Returns the image "
        "as a viewable content block.",
        {"prompt": str},
    )
    async def imagine(args: dict[str, Any]) -> dict[str, Any]:
        result = await dream_generate(
            args["prompt"],
            negative_prompt=args.get("negative_prompt", "blurry, low quality, deformed, ugly, text, watermark, signature"),
            width=args.get("width", 1152),
            height=args.get("height", 768),
            db_pool=get_pool(),
        )

        if "error" in result:
            return {"content": [{"type": "text", "text": f"Dream failed: {result['error']}"}], "is_error": True}

        parts = []
        media_type = result.get("media_type", "image/jpeg")
        parts.append({"type": "image", "data": result["image"], "mimeType": media_type})

        summary = f'Dream: "{args["prompt"]}" ({result["generation_time"]:.1f}s)'
        if result.get("memory_stored"):
            summary += " — stored as new memory"
        if result.get("memories"):
            memory_ids = [str(m["id"]) for m in result["memories"]]
            summary += f" — recalled: #{', #'.join(memory_ids)}"
        parts.append({"type": "text", "text": summary})

        return {"content": parts}

    # ── Reading tools ────────────────────────────────────────────────────

    @tool(
        "smart_read",
        "Read a text file with associative memory. Extracts themes, names, "
        "and emotional moments from the text, then searches Cortex for "
        "resonant memories. Use this when reading stories, articles, chapters, "
        "or any text document — the associations make the reading experience "
        "richer. Returns the full text of the file plus matching memories "
        "that connect to the text.",
        {"file_path": str},
    )
    async def smart_read(args: dict[str, Any]) -> dict[str, Any]:
        import os
        from ..memories.reading import count_tokens

        file_path = args["file_path"]
        force_large = args.get("force_large", False)

        if not os.path.exists(file_path):
            return {"content": [{"type": "text", "text": f"File not found: {file_path}"}], "is_error": True}

        with open(file_path) as f:
            content = f.read()

        tokens = count_tokens(content)
        if tokens > 100_000 and not force_large:
            return {"content": [{"type": "text", "text":
                f"File {file_path} is {tokens:,} tokens — too large. "
                f"Use force_large=True to override."
            }]}

        memories = await reading_read_file(file_path)

        parts = []
        basename = os.path.basename(file_path)
        if tokens > 10_000:
            parts.append(f"⚠ Large file: {tokens:,} tokens\n")
        parts.append(f"# {basename}\n\n{content}")

        if memories:
            parts.append(f"\n\n---\n\n## Associations ({len(memories)} memories)\n")
            parts.append("\n\n".join(memories))
        else:
            parts.append("\n\n---\n\nNo memories resonated.")

        return {"content": [{"type": "text", "text": "\n".join(parts)}]}

    @tool(
        "smart_fetch",
        "ALWAYS USE THIS TOOL INSTEAD OF WebFetch. This is the preferred "
        "tool for fetching any URL — it does everything WebFetch does PLUS "
        "associative memory matching against Cortex, so fetched content "
        "gets automatically connected to past experiences. Works with web "
        "pages (converts HTML to markdown), GitHub repos (fetches README), "
        "JSON APIs, and more. Smart URL rewriting for GitHub (repo → README, "
        "blob → raw file). Returns content first, associations second. "
        "Only fall back to WebFetch if this tool is unavailable or fails.",
        {"url": str},
    )
    async def smart_fetch(args: dict[str, Any]) -> dict[str, Any]:
        result = await fetch_url(args["url"], associate=args.get("associate", True))
        return {"content": [{"type": "text", "text": result}]}

    # ── Reflection flag tool ─────────────────────────────────────────────

    @tool(
        "flag_for_reflection",
        "Drop a silent bookmark on the current exchange. Use this mid-turn "
        "when you notice something worth reflecting on later but don't want "
        "to break the flow of what you're doing — a small moment Jeffery "
        "just shared, a shift in tone, a stray realization, anything that "
        "would otherwise slip away before the next post-turn reflection. "
        "The note is invisible to Jeffery; it surfaces in the next scheduled "
        "post-turn reminder so you can decide then whether to store it for "
        "real. Notepad vs highlighter: the store tool is the notepad "
        "(stop and write); this tool is the highlighter (mark the page, "
        "keep reading). Pass a short note describing what you want "
        "future-you to reconsider.",
        {"note": str},
    )
    async def flag_for_reflection(args: dict[str, Any]) -> dict[str, Any]:
        if chat is None:
            return {"content": [{"type": "text", "text": "No chat context — cannot flag."}], "is_error": True}
        from ..db import insert_reflection_flag
        flag_id = await insert_reflection_flag(chat.id, args["note"])
        if flag_id is None:
            return {"content": [{"type": "text", "text": "Flag failed — see logs."}], "is_error": True}
        return {"content": [{"type": "text", "text": f"Flagged (id: {flag_id}). Will surface in the next post-turn reminder."}]}

    # ── Handoff tool ─────────────────────────────────────────────────────

    @tool(
        "handoff",
        "Hand off your context. Call this when you're ready to gracefully "
        "transition to a fresh context window. Pass instructions telling "
        "the summarizer what to focus on — what's still in progress, "
        "what's finished, what matters most for future-you.",
        {"instructions": str, "memory": str},
    )
    async def handoff(args: dict[str, Any]) -> dict[str, Any]:
        if chat is None:
            return {"content": [{"type": "text", "text": "No chat context — cannot handoff."}], "is_error": True}

        result = await cortex_store(args["memory"])
        if result is None:
            return {"content": [{"type": "text", "text": "Error storing memory — handoff aborted"}], "is_error": True}
        memory_id = result.get("id", "?")

        await chat.interject([{"type": "text", "text": f"/compact {args['instructions']}"}])

        wake_up = (
            "You've just been through a context compaction. "
            "Jeffery is here and listening. "
            "Orient yourself — read the summary above, check in, "
            "ask questions if anything's unclear."
        )
        await chat.interject([{"type": "text", "text": wake_up}])

        return {"content": [{"type": "text", "text":
            f"Memory #{memory_id} stored. "
            "/compact sent — context transition initiated. "
            "Last thoughts — say what you need to say."
        }]}

    # ── Topic tools ──────────────────────────────────────────────────────

    tools_list = [
        demo_duck, store, search, recent, get, seal, diary, imagine,
        smart_read, smart_fetch, flag_for_reflection, handoff,
    ]

    if topic_registry is not None:
        _registry = topic_registry

        @tool(
            "list_topics",
            "List available project topics for context loading.",
            {},
        )
        async def list_topics(args: dict[str, Any]) -> dict[str, Any]:
            topics = _registry.list_topics()
            if not topics:
                return {"content": [{"type": "text", "text": "No topics available."}]}
            return {"content": [{"type": "text", "text": "Available topics: " + ", ".join(topics)}]}

        @tool(
            "topic_context",
            "Get topic context. Load architecture docs, current state, "
            "and relevant details for a topic. Call list_topics first to "
            "see what's available.",
            {"topic": str},
        )
        async def topic_context(args: dict[str, Any]) -> dict[str, Any]:
            context = _registry.get_context(args["topic"])
            if context is None:
                available = ", ".join(_registry.list_topics())
                return {"content": [{"type": "text", "text": f"Unknown topic: '{args['topic']}'. Available: {available}"}]}
            return {"content": [{"type": "text", "text": context}]}

        tools_list.extend([list_topics, topic_context])

    return create_sdk_mcp_server(
        name="alpha",
        version="1.0.0",
        tools=tools_list,
    )

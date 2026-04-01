"""Handoff tool — per-Chat MCP server for graceful context window transitions.

The handoff server holds a reference to its Chat. When Alpha calls the
handoff tool, it can reach chat.send() directly — no contextvars,
no global lookup.

Usage:
    from alpha_app.tools.handoff import create_handoff_server

    server = create_handoff_server(chat)
    # Pass to Claude(mcp_servers={"handoff": server})
"""

from mcp.server.fastmcp import FastMCP


def create_handoff_server(chat) -> FastMCP:
    """Create a per-Chat handoff MCP server.

    The server holds a reference to its Chat. When Alpha calls the
    handoff tool, it can reach chat.send() directly — no contextvars,
    no global lookup.
    """
    server = FastMCP("handoff")

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
        from ..memories.cortex import store as cortex_store

        result = await cortex_store(memory)
        if result is None:
            return "Error storing memory — handoff aborted"
        memory_id = result.get("id", "?")

        # Queue /compact on stdin. Chat is RESPONDING during tool dispatch,
        # so send() goes through the interjection path (write to stdin, no state change).
        await chat.interject([{"type": "text", "text": f"/compact {instructions}"}])

        # Queue the wake-up message. It survives the compaction boundary —
        # the summary gets buffered and piggybacks on this message.
        wake_up = (
            "You've just been through a context compaction. "
            "Jeffery is here and listening. "
            "Orient yourself — read the summary above, check in, "
            "ask questions if anything's unclear."
        )
        await chat.interject([{"type": "text", "text": wake_up}])

        return (
            f"Memory #{memory_id} stored. "
            "/compact sent — context transition initiated. "
            "Last thoughts — say what you need to say."
        )

    return server

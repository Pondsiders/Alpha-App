"""enrobe.py — Message enrichment pipeline.

The place where user messages get wrapped in memories, intro suggestions,
approach lights, and timestamps before being sent to Claude.

Currently a passthrough. Enrichment will be built here incrementally:
  1. Timestamp injection
  2. Approach lights (context usage warnings)
  3. Memory recall
  4. Intro speaks (suggest nudge from previous turn)

The name: to enrobe is to coat something in chocolate. The user message
is the truffle center; everything we add is the shell.
"""


async def enrobe(content: list[dict], *, chat_id: str = "") -> list[dict]:
    """Enrich a user message with context. Currently a passthrough.

    Args:
        content: The raw user message content blocks.
        chat_id: The chat ID (for future per-chat state like dedup tracking).

    Returns:
        Enriched content blocks ready to send to Claude.
    """
    return content

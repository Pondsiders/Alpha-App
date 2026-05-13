"""A snapshot of the app's globally-visible state.

`snapshot()` returns the data carried by both the `hi-yourself` response
and the `app-state` event. Callers wrap it in the appropriate envelope:
the hello handler builds a `HiYourself` response; broadcasters build an
`AppState` event.
"""

import alpha
from alpha import chats
from alpha.ws.events import ChatSummary


async def snapshot() -> tuple[str, list[ChatSummary]]:
    """Return the version + chat-summary list as they are right now."""
    summaries = [chats.summary_of(chat) for chat in await chats.all()]
    return alpha.__version__, summaries

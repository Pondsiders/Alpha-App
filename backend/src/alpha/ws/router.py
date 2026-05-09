"""WebSocket endpoint — accepts connections, pushes initial state, dispatches.

The doorman is the bouncer: this module is where raw JSON crosses into
validated `BaseCommand` instances. Wire-shape failures (bad JSON, missing
fields, unknown commands) are bugs — they raise uncaught exceptions and
FastAPI closes the socket. The frontend reconnects; the trace lands in
Logfire under the request span. See `wire-protocol.md`.

`error` events on the wire are reserved for *domain* failures — valid
commands that can't be done (chat not found, subprocess died, etc.).
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import alpha
from alpha import chats
from alpha.chat import Chat
from alpha.ws.commands import CommandAdapter
from alpha.ws.dispatcher import Dispatcher
from alpha.ws.events import AppState, ChatSummary

router = APIRouter()


def _summarize(chat: Chat) -> ChatSummary:
    """Project a Chat into the ChatSummary the wire carries.

    Runtime fields (`state`, `tokenCount`) are placeholders for now — no
    SDK process exists yet to wake, no token meter wired. They land here
    as `pending` and `0` and become live when chat-state events arrive
    from the SDK lifecycle. The wire shape stays stable across that
    transition; only the values evolve.
    """
    return ChatSummary(
        chat_id=chat.chat_id,
        created_at=chat.created_at,
        last_active=chat.last_active,
        state="pending",
        token_count=0,
        context_window=1_000_000,
    )


@router.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    """Accept a WebSocket, push initial app-state, route inbound messages."""
    await websocket.accept()

    summaries = [_summarize(chat) for chat in await chats.all()]
    app_state = AppState(chats=summaries, version=alpha.__version__)
    await websocket.send_json(
        app_state.model_dump(by_alias=True, exclude_none=True, mode="json")
    )

    dispatcher = Dispatcher()

    try:
        while True:
            msg = await websocket.receive_json()
            command = CommandAdapter.validate_python(msg)
            await dispatcher.dispatch(websocket, command)
    except WebSocketDisconnect:
        return

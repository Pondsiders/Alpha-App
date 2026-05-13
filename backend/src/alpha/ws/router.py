"""WebSocket endpoint — accepts connections and dispatches commands.

The doorman is the bouncer: this module is where raw JSON crosses into
validated `BaseCommand` instances. Wire-shape failures (bad JSON, missing
fields, unknown commands) are bugs — they raise uncaught exceptions and
FastAPI closes the socket. The frontend reconnects; the trace lands in
Logfire under the request span. See `wire-protocol.md`.

`error` events on the wire are reserved for *domain* failures — valid
commands that can't be done (chat not found, subprocess died, etc.).
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import alpha.ws.connections as connections
from alpha.ws.commands import CommandAdapter
from alpha.ws.dispatcher import Dispatcher

router = APIRouter()


@router.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    """Accept a WebSocket and route inbound commands."""
    await websocket.accept()
    await connections.register(websocket)

    dispatcher = Dispatcher()

    try:
        while True:
            msg = await websocket.receive_json()
            command = CommandAdapter.validate_python(msg)
            await dispatcher.dispatch(websocket, command)
    except WebSocketDisconnect:
        return
    finally:
        await connections.unregister(websocket)

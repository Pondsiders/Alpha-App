"""WebSocket endpoint — accepts connections, forwards messages to the Dispatcher.

Per-message logfire spans replace the connection-level span we excluded
in `app.create_app()`. Bad JSON gets one structured error event and the
connection stays open; everything else flows through the Dispatcher.
"""

from json import JSONDecodeError

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from alpha.ws.dispatcher import Dispatcher
from alpha.ws.events import Error

router = APIRouter()


@router.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    """Accept a WebSocket and route inbound messages."""
    await websocket.accept()

    dispatcher = Dispatcher()

    try:
        while True:
            try:
                msg = await websocket.receive_json()
            except JSONDecodeError:
                await websocket.send_json(
                    Error(
                        code="invalid-json",
                        message="Message body was not valid JSON.",
                    ).model_dump(by_alias=True, exclude_none=True)
                )
                continue

            await dispatcher.dispatch(websocket, msg)
    except WebSocketDisconnect:
        return

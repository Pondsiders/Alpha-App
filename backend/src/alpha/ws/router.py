"""WebSocket endpoint — accepts connections, says hello.

Stub. Real wire-protocol handling lands here as the spec settles.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    """Accept a WebSocket, send a hello, hold the line."""
    await websocket.accept()
    await websocket.send_json({"type": "hello"})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        return

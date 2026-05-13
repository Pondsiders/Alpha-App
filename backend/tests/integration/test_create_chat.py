"""Integration test for the `create-chat` WebSocket round-trip."""

from typing import Any

import psycopg
from fastapi.testclient import TestClient
from syrupy.assertion import SnapshotAssertion

from alpha.settings import settings


def test_create_chat_round_trip(
    client: TestClient, snapshot: SnapshotAssertion
) -> None:
    """Send `create-chat`, expect `chat-created`, verify row in `app.chats`."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"command": "create-chat", "id": "req_1"})
        response: dict[str, Any] = ws.receive_json()

    assert response["response"] == "chat-created"
    assert response["id"] == "req_1"
    assert isinstance(response["chatId"], str)
    assert len(response["chatId"]) == 21

    # The row landed in app.chats with the same id we got back.
    with psycopg.connect(settings.database_url) as conn, conn.cursor() as cur:
        _ = cur.execute(
            "SELECT chat_id, session_id, archived FROM app.chats WHERE chat_id = %s",
            (response["chatId"],),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == response["chatId"]
        assert row[1] is None
        assert row[2] is False

    masked = {**response, "chatId": "<chat-id>"}
    assert masked == snapshot

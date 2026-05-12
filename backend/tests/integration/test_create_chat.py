"""Integration test for the `create-chat` WebSocket round-trip."""

from datetime import datetime
from typing import Any

import psycopg
from fastapi.testclient import TestClient
from syrupy.assertion import SnapshotAssertion

from alpha.settings import settings
from alpha.ws.commands import CreateChat


def test_create_chat_round_trip(
    client: TestClient, snapshot: SnapshotAssertion
) -> None:
    """Send `create-chat`, expect `chat-created`, verify row in `app.chats`."""
    cmd = CreateChat(command="create-chat", id="req_1")
    with client.websocket_connect("/ws") as ws:
        ws.send_text(cmd.model_dump_json(by_alias=True))
        event: dict[str, Any] = ws.receive_json()

    # Per-field assertions for values that have to be specific.
    assert isinstance(event["chatId"], str)
    assert len(event["chatId"]) == 21
    created_at = datetime.fromisoformat(event["createdAt"])
    last_active = datetime.fromisoformat(event["lastActive"])
    assert created_at.tzinfo is not None
    assert last_active.tzinfo is not None
    assert event["state"] == "pending"
    assert event["tokenCount"] == 0
    assert event["contextWindow"] == 1_000_000
    assert event["archived"] is False
    assert "id" not in event

    # The row landed in app.chats with the same id we got back.
    with psycopg.connect(settings.database_url) as conn, conn.cursor() as cur:
        _ = cur.execute(
            "SELECT chat_id, session_id, archived FROM app.chats WHERE chat_id = %s",
            (event["chatId"],),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == event["chatId"]
        assert row[1] is None
        assert row[2] is False

    # Snapshot the wire shape with non-deterministic fields masked. Adding a
    # field to ChatCreated will fail this assertion until the snapshot is
    # updated via `pytest --snapshot-update`. The diff is the audit trail.
    masked = {
        **event,
        "chatId": "<chat-id>",
        "createdAt": "<timestamp>",
        "lastActive": "<timestamp>",
    }
    assert masked == snapshot

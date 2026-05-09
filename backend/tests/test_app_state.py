"""Integration test for the connect-time `app-state` push."""

from typing import Any

from fastapi.testclient import TestClient

from alpha.ws.events import AppState


def test_app_state_pushed_on_connect_empty(client: TestClient) -> None:
    """On a fresh database, the first event is `app-state` with no chats."""
    with client.websocket_connect("/ws") as ws:
        event: dict[str, Any] = ws.receive_json()

    assert event["event"] == "app-state"
    assert event["chats"] == []
    parsed = AppState.model_validate(event)
    assert parsed.chats == []


def test_app_state_pushed_on_connect_with_chats(client: TestClient) -> None:
    """After creating a chat, app-state on a new connect lists it."""
    with client.websocket_connect("/ws") as ws:
        _ = ws.receive_json()  # initial empty app-state
        ws.send_json({"command": "create-chat", "id": "req_1"})
        created = ws.receive_json()

    assert created["event"] == "chat-created"
    new_chat_id = created["chatId"]

    with client.websocket_connect("/ws") as ws:
        event: dict[str, Any] = ws.receive_json()

    assert event["event"] == "app-state"
    assert len(event["chats"]) == 1
    summary = event["chats"][0]
    assert summary["chatId"] == new_chat_id
    assert summary["state"] == "dead"
    assert summary["tokenCount"] == 0
    assert summary["contextWindow"] == 1_000_000

"""Integration test for the client-initiated hello / hi-yourself handshake."""

from typing import Any

from fastapi.testclient import TestClient

from alpha.ws.responses import HiYourself


def test_hello_returns_hi_yourself(client: TestClient) -> None:
    """Hello echoes the id and returns the current chat list and version."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"command": "hello", "id": "req_0"})
        response: dict[str, Any] = ws.receive_json()

    assert response["response"] == "hi-yourself"
    assert response["id"] == "req_0"
    assert isinstance(response["chats"], list)
    assert isinstance(response["version"], str)
    parsed = HiYourself.model_validate(response)
    assert parsed.id == "req_0"


def test_hello_includes_a_created_chat(client: TestClient) -> None:
    """A chat created in this session appears in a subsequent hello."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"command": "hello", "id": "req_0"})
        _ = ws.receive_json()
        ws.send_json({"command": "create-chat", "id": "req_1"})
        created = ws.receive_json()

    assert created["event"] == "chat-created"
    new_chat_id = created["chatId"]

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"command": "hello", "id": "req_0"})
        response: dict[str, Any] = ws.receive_json()

    assert response["response"] == "hi-yourself"
    ids = [c["chatId"] for c in response["chats"]]
    assert new_chat_id in ids
    summary = next(c for c in response["chats"] if c["chatId"] == new_chat_id)
    assert summary["state"] == "pending"
    assert summary["tokenCount"] == 0
    assert summary["contextWindow"] == 1_000_000

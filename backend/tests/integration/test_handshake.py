"""Integration test for the client-initiated hello / hi-yourself handshake."""

from typing import Any

from fastapi.testclient import TestClient

from alpha.ws.responses import HiYourself


def test_hello_empty(client: TestClient) -> None:
    """On a fresh database, hello returns hi-yourself with no chats."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"command": "hello", "id": "req_0"})
        response: dict[str, Any] = ws.receive_json()

    assert response["response"] == "hi-yourself"
    assert response["id"] == "req_0"
    assert response["chats"] == []
    parsed = HiYourself.model_validate(response)
    assert parsed.chats == []


def test_hello_with_a_chat(client: TestClient) -> None:
    """After creating a chat, a fresh hello lists exactly that chat."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"command": "hello", "id": "req_0"})
        _ = ws.receive_json()
        ws.send_json({"command": "create-chat", "id": "req_1"})
        created = ws.receive_json()

    assert created["response"] == "chat-created"
    new_chat_id = created["chatId"]

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"command": "hello", "id": "req_0"})
        response: dict[str, Any] = ws.receive_json()

    assert response["response"] == "hi-yourself"
    assert len(response["chats"]) == 1
    summary = response["chats"][0]
    assert summary["chatId"] == new_chat_id
    assert summary["state"] == "pending"
    assert summary["tokenCount"] == 0
    assert summary["contextWindow"] == 1_000_000

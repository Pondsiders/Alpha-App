"""Test: WebSocket connect delivers app-state + chat-loaded immediately.

Verifies the zero-round-trip startup protocol:
1. Connect to /ws (optionally with ?lastChat=)
2. Receive app-state event (chat list, global flags)
3. Receive chat-loaded event (full message history for one chat)
No client command needed — the server pushes on connect.
"""

import json

import pytest
import websockets


@pytest.mark.asyncio
async def test_connect_receives_app_state_and_chat_loaded(ws_url: str):
    """On connect, server sends app-state then chat-loaded."""
    async with websockets.connect(ws_url) as ws:
        # First message: app-state
        raw1 = await ws.recv()
        msg1 = json.loads(raw1)
        assert msg1["event"] == "app-state", f"Expected app-state, got {msg1.get('event')}"
        assert "chats" in msg1
        assert isinstance(msg1["chats"], list)
        assert len(msg1["chats"]) > 0, "Chat list should not be empty"
        assert "solitude" in msg1
        assert "version" in msg1

        # Verify seeded chat is in the list
        chat_ids = [c["chatId"] for c in msg1["chats"]]
        assert "testchat01" in chat_ids

        # Second message: chat-loaded
        raw2 = await ws.recv()
        msg2 = json.loads(raw2)
        assert msg2["event"] == "chat-loaded", f"Expected chat-loaded, got {msg2.get('event')}"
        assert msg2["chatId"] == "testchat01"
        assert msg2["title"] == "Test Chat"
        assert isinstance(msg2["messages"], list)
        assert len(msg2["messages"]) == 2

        # Verify message content
        user_msg = msg2["messages"][0]
        assert user_msg["role"] == "user"

        asst_msg = msg2["messages"][1]
        assert asst_msg["role"] == "assistant"


@pytest.mark.asyncio
async def test_connect_with_lastchat_hint(ws_url: str):
    """?lastChat= parameter suggests which chat to load."""
    url = f"{ws_url}?lastChat=testchat01"
    async with websockets.connect(url) as ws:
        raw1 = await ws.recv()
        msg1 = json.loads(raw1)
        assert msg1["event"] == "app-state"

        raw2 = await ws.recv()
        msg2 = json.loads(raw2)
        assert msg2["event"] == "chat-loaded"
        assert msg2["chatId"] == "testchat01"


@pytest.mark.asyncio
async def test_connect_with_bogus_lastchat_falls_back(ws_url: str):
    """Invalid ?lastChat= falls back to most recent chat."""
    url = f"{ws_url}?lastChat=nonexistent999"
    async with websockets.connect(url) as ws:
        raw1 = await ws.recv()
        msg1 = json.loads(raw1)
        assert msg1["event"] == "app-state"

        raw2 = await ws.recv()
        msg2 = json.loads(raw2)
        assert msg2["event"] == "chat-loaded"
        # Should get testchat01 (only chat in DB) regardless of bogus hint
        assert msg2["chatId"] == "testchat01"

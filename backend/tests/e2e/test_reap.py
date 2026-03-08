"""Reap lifecycle tests — the birthday bug and its fix.

On March 7, 2026, Alpha found that the reap timer had NEVER WORKED.
_cancel_reap_timer() cancelled its own asyncio task when called from
_reap_after(), preventing reap() from ever setting state = DEAD.
Python 3.9+ CancelledError is BaseException, not Exception, so the
except Exception in reap() didn't catch it. The chat stayed IDLE forever.

Two tests, two levels:
  1. Protocol test — WebSocket client, no browser. Proves the reap timer
     fires and chat-state:dead arrives over the wire.
  2. UI test — Playwright. Proves the sidebar dot goes green → gray.

Both run against the same subprocess backend with _ALPHA_REAP_TIMEOUT=15s
(set in conftest.py). The protocol test is fast. The UI test is insurance.

Run with:
    cd Alpha-App/frontend && npm run build
    cd Alpha-App/backend && uv run pytest tests/e2e/test_reap.py -v
"""

import json
import re
import time
from pathlib import Path

from playwright.sync_api import Page, expect
from websockets.sync.client import connect as ws_connect

# Reuse constants from the streaming tests.
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
INPUT_SELECTOR = '[placeholder="Message Alpha..."]'
ASSISTANT_MSG_SELECTOR = ".group\\/assistant"
MODEL_TIMEOUT = 15_000
NAV_TIMEOUT = 5_000

# The backend runs with _ALPHA_REAP_TIMEOUT=15 (set in conftest.py).
# Wait long enough for the timer to fire + asyncio scheduling + WebSocket delivery.
REAP_TIMEOUT_S = 15
REAP_WAIT_S = REAP_TIMEOUT_S + 10  # generous buffer


def _screenshot(page: Page, name: str) -> None:
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_DIR / f"{name}.png"), full_page=True)


def _enter_chat(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/chat")
    page.wait_for_url(re.compile(r"/chat/.+"), timeout=NAV_TIMEOUT)


# ---------------------------------------------------------------------------
# Level 1: Protocol test — WebSocket, no browser
# ---------------------------------------------------------------------------


def test_reap_lifecycle_protocol(base_url: str) -> None:
    """Protocol test: reap timer fires, chat-state:dead arrives via WebSocket.

    No browser. No UI. Just the wire protocol. If this fails, the reap
    timer is broken at the backend level — the birthday bug (March 7, 2026).

    Uses synchronous WebSocket (websockets.sync.client) to avoid event loop
    conflicts with Playwright's sync fixtures. The original async version
    hit RuntimeError: Runner.run() cannot be called from a running event loop.
    """
    ws_url = base_url.replace("http://", "ws://") + "/ws"

    with ws_connect(ws_url) as ws:
        # Create a chat
        ws.send(json.dumps({"type": "create-chat"}))
        msg = json.loads(ws.recv(timeout=30))
        assert msg["type"] == "chat-created", f"Expected chat-created, got {msg['type']}"
        chat_id = msg["chatId"]

        # Send a message
        ws.send(json.dumps({
            "type": "send",
            "chatId": chat_id,
            "content": "\u00a7echo:Reap me if you can",
        }))

        # Drain events until the turn completes.
        # We'll see chat-state:busy, text-delta(s), chat-state:idle, done.
        got_idle = False
        while True:
            raw = ws.recv(timeout=30)
            msg = json.loads(raw)
            if msg["type"] == "chat-state" and msg.get("chatId") == chat_id:
                if msg["data"]["state"] == "idle":
                    got_idle = True
            if msg["type"] == "done" and msg.get("chatId") == chat_id:
                break

        assert got_idle, "Never saw chat-state:idle after the turn"

        # Now wait for the reap timer to fire.
        # The backend should send chat-state:dead when the timer fires.
        deadline = time.monotonic() + REAP_WAIT_S
        got_dead = False
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                raw = ws.recv(timeout=remaining)
                msg = json.loads(raw)
                if (
                    msg["type"] == "chat-state"
                    and msg.get("chatId") == chat_id
                    and msg["data"]["state"] == "dead"
                ):
                    got_dead = True
                    break
            except TimeoutError:
                break

        assert got_dead, (
            f"Reap timer did not fire within {REAP_WAIT_S}s. "
            f"The birthday bug is back."
        )


# ---------------------------------------------------------------------------
# Level 2: UI test — Playwright, sidebar dot green → gray
# ---------------------------------------------------------------------------


def test_reap_lifecycle_ui(page: Page, base_url: str) -> None:
    """UI test: sidebar dot goes green → gray when the reap timer fires.

    Full end-to-end: browser, WebSocket, real sidebar, real CSS. If the
    protocol test passes but this fails, the frontend isn't handling
    chat-state:dead events for the indicator dot.

    Steps:
    1. Open browser, enter a chat (auto-created)
    2. Send a message, wait for response (chat is IDLE, dot is green)
    3. Wait for reap timer to fire (15s + buffer)
    4. Assert the sidebar dot turned gray (bg-neutral-500)
    """
    _enter_chat(page, base_url)

    input_box = page.locator(INPUT_SELECTOR)
    expect(input_box).to_be_visible(timeout=NAV_TIMEOUT)

    # Send a message so the chat has a title and the turn completes
    input_box.fill("\u00a7echo:The frog goes ribbit")
    input_box.press("Enter")

    assistant_msg = page.locator(ASSISTANT_MSG_SELECTOR).first
    expect(assistant_msg).to_be_visible(timeout=MODEL_TIMEOUT)
    expect(assistant_msg).to_contain_text("The frog goes ribbit")

    _screenshot(page, "reap_01_response_received")

    # Verify the dot is green (idle) in the sidebar
    sidebar = page.locator("[data-sidebar='sidebar']")
    chat_button = sidebar.locator("button").filter(has_text="The frog goes ribbit")
    expect(chat_button).to_be_visible(timeout=NAV_TIMEOUT)

    idle_dot = chat_button.locator('span[aria-label="idle"]')
    expect(idle_dot).to_be_visible(timeout=NAV_TIMEOUT)

    _screenshot(page, "reap_02_green_dot")

    # Wait for the reap timer to fire.
    # _ALPHA_REAP_TIMEOUT = 15s. Add buffer for scheduling + delivery.
    page.wait_for_timeout(REAP_WAIT_S * 1000)

    # The dot should now be gray (dead).
    # The aria-label changes from "idle" to "dead" when chat-state:dead arrives.
    dead_dot = chat_button.locator('span[aria-label="dead"]')
    expect(dead_dot).to_be_visible(timeout=5_000)

    _screenshot(page, "reap_03_gray_dot_after_reap")

"""Happy path — open the app, send a message, get a response."""

import json
from pathlib import Path

from playwright.sync_api import Page, expect

SCREENSHOT_DIR = Path(__file__).parent / "screenshots"


def test_happy_path(page: Page, base_url: str) -> None:
    import re

    SCREENSHOT_DIR.mkdir(exist_ok=True)

    # Hook WebSocket BEFORE navigating so we catch the connection
    done_messages = []

    def on_ws(ws):
        def on_frame(payload):
            try:
                msg = json.loads(payload)
                if msg.get("type") == "done":
                    done_messages.append(msg)
            except (json.JSONDecodeError, TypeError):
                pass
        ws.on("framereceived", lambda payload: on_frame(payload))

    page.on("websocket", on_ws)

    # Navigate to /chat, which redirects to /chat/{new_id}
    page.goto(f"{base_url}/chat")
    page.wait_for_url(re.compile(r"/chat/.+"), timeout=5_000)

    # Send a message
    input_box = page.locator('[placeholder="Message Alpha..."]')
    expect(input_box).to_be_visible(timeout=5_000)
    input_box.fill("Hello")
    input_box.press("Enter")

    # Poll until the "done" WebSocket message arrives (turn complete)
    elapsed = 0
    while not done_messages and elapsed < 15_000:
        page.wait_for_timeout(200)
        elapsed += 200

    assert done_messages, "Never received 'done' WebSocket message"

    # Verify an assistant message appeared
    assistant = page.locator(".group\\/assistant").first
    expect(assistant).to_be_visible(timeout=5_000)

    page.screenshot(path=str(SCREENSHOT_DIR / "happy_path.png"), full_page=True)

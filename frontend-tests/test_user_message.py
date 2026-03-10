"""Test: UserMessage component renders correctly."""

import re

from playwright.sync_api import expect
from conftest import CHAT_ID, ws_event


def test_user_message(page, send, screenshots):
    """Send a user-message event, verify the UserMessage component renders."""
    # Send one user-message event
    send(ws_event("user-message", CHAT_ID, {"content": [
        {"type": "text", "text": "Tell me about ducks"},
    ]}))

    # Wait for it to render
    el = page.locator('[data-testid="user-message"]')
    expect(el).to_be_visible(timeout=5000)
    page.wait_for_timeout(200)

    # Screenshot the window
    page.screenshot(path=str(screenshots / "test_user_message.png"))

    # -- Assertions against the root element (MessagePrimitive.Root) ----------
    # Right-aligned flex column with bottom margin and gap
    expect(el).to_have_class(re.compile(r"\bflex\b"))
    expect(el).to_have_class(re.compile(r"\bflex-col\b"))
    expect(el).to_have_class(re.compile(r"\bitems-end\b"))
    expect(el).to_have_class(re.compile(r"\bmb-4\b"))
    expect(el).to_have_class(re.compile(r"\bgap-2\b"))

    # Exactly one user message on the page
    expect(page.locator('[data-testid="user-message"]')).to_have_count(1)

    # -- Assertions against the text bubble (inner div) -----------------------
    bubble = el.locator("div").first

    # Content
    expect(bubble).to_have_text("Tell me about ducks")

    # Tailwind classes on the text bubble
    expect(bubble).to_have_class(re.compile(r"\bpx-4\b"))
    expect(bubble).to_have_class(re.compile(r"\bpy-3\b"))
    expect(bubble).to_have_class(re.compile(r"\bbg-user-bubble\b"))
    expect(bubble).to_have_class(re.compile(r"\brounded-2xl\b"))
    expect(bubble).to_have_class(re.compile(r"max-w-\[75%\]"))
    expect(bubble).to_have_class(re.compile(r"\btext-text\b"))
    expect(bubble).to_have_class(re.compile(r"\bbreak-words\b"))
    expect(bubble).to_have_class(re.compile(r"\bwhitespace-pre-wrap\b"))

    # No image bubbles (we didn't send any image content)
    expect(el.locator("img")).to_have_count(0)

"""Test: AssistantMessage component renders correctly."""

import re

from playwright.sync_api import expect
from conftest import CHAT_ID, ws_event


def test_assistant_message(page, send, screenshots):
    """Send text-delta and done events, verify the AssistantMessage component renders."""
    send(ws_event("text-delta", CHAT_ID, "Hello from the duck side"))
    send(ws_event("done", CHAT_ID))

    el = page.locator('[data-testid="assistant-message"]')
    expect(el).to_be_visible(timeout=5000)
    page.wait_for_timeout(200)

    page.screenshot(path=str(screenshots / "test_assistant_message.png"))

    expect(el).to_have_class(re.compile(r"\bmb-6\b"))
    expect(el).to_have_class(re.compile(r"\bpl-2\b"))
    expect(el).to_have_class(re.compile(r"\bpr-12\b"))
    expect(el).to_have_class(re.compile(r"group/assistant"))

    expect(page.locator('[data-testid="assistant-message"]')).to_have_count(1)

    content_div = el.locator("div").first
    expect(content_div).to_have_class(re.compile(r"\btext-text\b"))
    expect(content_div).to_have_class(re.compile(r"\bleading-relaxed\b"))

    expect(el).to_contain_text("Hello from the duck side")

    copy_button = el.locator('button[aria-label="Copy message"]')
    expect(copy_button).to_be_attached()

    expect(page.locator('[data-testid="thinking-block"]')).to_have_count(0)
    expect(page.locator('[data-testid="tool-call"]')).to_have_count(0)
    expect(page.locator('[data-testid="memory-note"]')).to_have_count(0)

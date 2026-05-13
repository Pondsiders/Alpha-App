"""End-to-end test for the send-and-reply journey."""

import re

from playwright.sync_api import Page, expect


def test_user_sends_a_message_and_gets_an_assistant_response(
    page: Page, backend: str
) -> None:
    """Send a user message; assert presence of both the echo and a reply."""
    _ = page.goto(backend)

    new_chat = page.get_by_role("button", name="New Chat")
    expect(new_chat).to_be_visible()
    new_chat.click()

    # The new chat appears in the sidebar — chat items render their
    # creation time as a label matching "H:MM AM" or "H:MM PM".
    chat_item_pattern = re.compile(r"^\d{1,2}:\d{2}\s+(AM|PM)$")
    expect(page.get_by_role("button", name=chat_item_pattern)).to_have_count(1)

    assistant_messages = page.locator('[data-role="assistant"]')
    expect(assistant_messages).to_have_count(0)

    page.get_by_label("Message input").fill("Hello, Alpha.")
    page.get_by_label("Send message").click()

    expect(page.locator('[data-role="user"]')).to_have_count(1)
    expect(assistant_messages).to_have_count(1)

"""End-to-end test for the new-chat-send-reload journey."""

import re

from playwright.sync_api import Page, expect


def test_new_chat_send_reload(page: Page, backend: str) -> None:
    """Create a chat, send a message, reload, see the chat and its messages."""
    _ = page.goto(backend)

    new_chat = page.get_by_role("button", name="New Chat")
    expect(new_chat).to_be_visible()
    new_chat.click()

    # The new chat appears in the sidebar — chat items render their
    # creation time as a label matching "H:MM AM" or "H:MM PM".
    chat_item_pattern = re.compile(r"^\d{1,2}:\d{2}\s+(AM|PM)$")
    chat_item = page.get_by_role("button", name=chat_item_pattern)
    expect(chat_item).to_have_count(1)

    page.get_by_label("Message input").fill("Hello, Alpha.")
    page.get_by_label("Send message").click()

    expect(page.locator('[data-role="user"]')).to_have_count(1)
    expect(page.locator('[data-role="assistant"]')).to_have_count(1)

    _ = page.reload()

    expect(chat_item).to_have_count(1)
    chat_item.click()
    expect(page.locator('[data-role="assistant"]')).to_have_count(1)

"""Test: ThinkingBlock component renders correctly."""

import re

from playwright.sync_api import expect
from conftest import CHAT_ID, ws_event


def test_thinking_block(page, send, screenshots):
    """Send thinking-delta and done events, verify the ThinkingBlock component renders."""
    send(ws_event("thinking-delta", CHAT_ID, "Let me consider the implications of duck-based economics..."))
    send(ws_event("done", CHAT_ID))

    el = page.locator('[data-testid="thinking-block"]')
    expect(el).to_be_visible(timeout=5000)
    page.wait_for_timeout(200)

    page.screenshot(path=str(screenshots / "test_thinking_block.png"))

    # Verify ThinkingBlock is a details element
    expect(el).to_have_attribute("data-testid", "thinking-block")
    tag_name = el.evaluate("el => el.tagName.toLowerCase()")
    assert tag_name == "details", f"Expected details element, got {tag_name}"

    # Verify root classes
    class_attr = el.get_attribute("class")
    assert re.search(r"\bgroup\b", class_attr), "Missing class: group"

    # Verify summary element exists
    summary = el.locator("summary")
    expect(summary).to_be_visible()

    # Verify summary classes
    summary_class = summary.get_attribute("class")
    assert re.search(r"\bcursor-pointer\b", summary_class), "Missing class: cursor-pointer"
    assert re.search(r"\btext-muted\b", summary_class), "Missing class: text-muted"
    assert re.search(r"\bitalic\b", summary_class), "Missing class: italic"
    assert re.search(r"\bselect-none\b", summary_class), "Missing class: select-none"
    assert re.search(r"\blist-none\b", summary_class), "Missing class: list-none"
    assert re.search(r"\bflex\b", summary_class), "Missing class: flex"
    assert re.search(r"\bitems-center\b", summary_class), "Missing class: items-center"
    assert re.search(r"\bgap-2\b", summary_class), "Missing class: gap-2"
    assert re.search(r"text-\[13px\]", summary_class), "Missing class: text-[13px]"

    # Verify summary text says "Thought" (NOT "Thinking...")
    summary_text = summary.inner_text()
    assert "Thought" in summary_text, f"Expected 'Thought' in summary, got: {summary_text}"
    assert "Thinking..." not in summary_text, f"Should not contain 'Thinking...', got: {summary_text}"

    # Verify arrow span exists with text "▶"
    arrow_span = summary.locator("span").first
    expect(arrow_span).to_have_text("▶")

    # Verify arrow span classes
    arrow_class = arrow_span.get_attribute("class")
    assert re.search(r"text-muted/60", arrow_class), "Missing class: text-muted/60"
    assert re.search(r"\btransition-transform\b", arrow_class), "Missing class: transition-transform"
    assert re.search(r"\binline-block\b", arrow_class), "Missing class: inline-block"

    # Verify content div exists with the thinking text
    # (content is inside a collapsed <details> — not visible until opened)
    content_div = el.locator("div")
    expect(content_div).to_have_count(1)
    content_text = content_div.text_content()
    assert content_text == "Let me consider the implications of duck-based economics...", \
        f"Expected thinking text, got: {content_text}"

    # Verify content div classes
    content_class = content_div.get_attribute("class")
    assert re.search(r"\bmt-2\b", content_class), "Missing class: mt-2"
    assert re.search(r"\bpl-4\b", content_class), "Missing class: pl-4"
    assert re.search(r"\bborder-l-2\b", content_class), "Missing class: border-l-2"
    assert re.search(r"border-muted/20", content_class), "Missing class: border-muted/20"
    assert re.search(r"\btext-muted\b", content_class), "Missing class: text-muted"
    assert re.search(r"\bitalic\b", content_class), "Missing class: italic"
    assert re.search(r"\bleading-relaxed\b", content_class), "Missing class: leading-relaxed"
    assert re.search(r"\bwhitespace-pre-wrap\b", content_class), "Missing class: whitespace-pre-wrap"
    assert re.search(r"text-\[13px\]", content_class), "Missing class: text-[13px]"

    # Verify exactly one thinking-block on the page
    all_thinking_blocks = page.locator('[data-testid="thinking-block"]')
    expect(all_thinking_blocks).to_have_count(1)

    # Verify the thinking block is inside an assistant-message container
    assistant_message = page.locator('[data-testid="assistant-message"]')
    expect(assistant_message).to_be_visible()
    thinking_in_assistant = assistant_message.locator('[data-testid="thinking-block"]')
    expect(thinking_in_assistant).to_be_visible()

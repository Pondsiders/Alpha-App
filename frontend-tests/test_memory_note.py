"""Test: MemoryNote component renders correctly."""

import re

from playwright.sync_api import expect
from conftest import CHAT_ID, ws_event


def test_memory_note(page, send, screenshots):
    """Send cortex store tool-call and tool-result events, verify the MemoryNote component renders."""
    send(ws_event("tool-call", CHAT_ID, {
        "toolCallId": "tc_store_1",
        "toolName": "mcp__cortex__store",
        "args": {"memory": "Ducks are magnificent creatures with corkscrew anatomy"},
        "argsText": '{"memory": "Ducks are magnificent creatures with corkscrew anatomy"}'
    }))

    send(ws_event("tool-result", CHAT_ID, {
        "toolCallId": "tc_store_1",
        "result": "Memory stored (id: 42)"
    }))

    send(ws_event("done", CHAT_ID))

    el = page.locator('[data-testid="memory-note"]')
    expect(el).to_be_visible(timeout=5000)
    page.wait_for_timeout(200)

    page.screenshot(path=str(screenshots / "test_memory_note.png"))

    # Root element classes (gap-based spacing — no individual margins)
    expect(el).to_have_class(re.compile(r"\bflex\b"))
    expect(el).to_have_class(re.compile(r"\bitems-start\b"))
    expect(el).to_have_class(re.compile(r"\bgap-2\b"))

    # Feather icon (SVG element inside root)
    svg = el.locator("svg").first
    expect(svg).to_be_visible()

    # SVG should NOT have animate-pulse-dot class (streaming is done)
    expect(svg).not_to_have_class(re.compile(r"\banimate-pulse-dot\b"))

    # Text wrapper div
    text_wrapper = el.locator("div.relative.overflow-hidden.min-w-0.flex-1")
    expect(text_wrapper).to_be_visible()
    expect(text_wrapper).to_have_class(re.compile(r"\brelative\b"))
    expect(text_wrapper).to_have_class(re.compile(r"\boverflow-hidden\b"))
    expect(text_wrapper).to_have_class(re.compile(r"\bmin-w-0\b"))
    expect(text_wrapper).to_have_class(re.compile(r"\bflex-1\b"))

    # Text content div
    text_content = text_wrapper.locator("div").first
    expect(text_content).to_be_visible()
    expect(text_content).to_have_class(re.compile(r"\bitalic\b"))
    expect(text_content).to_have_class(re.compile(r"\bleading-snug\b"))
    expect(text_content).to_have_class(re.compile(r"\bwhitespace-pre-wrap\b"))
    expect(text_content).to_have_class(re.compile(r"\bbreak-words\b"))
    expect(text_content).to_have_class(re.compile(r"text-\[13px\]"))
    expect(text_content).to_have_class(re.compile(r"text-muted/60"))

    # Text content
    expect(text_content).to_contain_text("Ducks are magnificent creatures with corkscrew anatomy")

    # Exactly one memory-note on the page
    expect(page.locator('[data-testid="memory-note"]')).to_have_count(1)

    # Memory note is inside an assistant-message container
    assistant_message = page.locator('[data-testid="assistant-message"]')
    expect(assistant_message).to_be_visible()
    expect(assistant_message.locator('[data-testid="memory-note"]')).to_have_count(1)

    # No tool-call elements present (MemoryNote replaces ToolFallback for cortex store)
    expect(page.locator('[data-testid="tool-call"]')).to_have_count(0)

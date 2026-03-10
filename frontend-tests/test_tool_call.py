"""Test: ToolFallback component renders correctly."""

import re

from playwright.sync_api import expect
from conftest import CHAT_ID, ws_event


def test_tool_call(page, send, screenshots):
    """Send tool-call and tool-result events, verify the ToolFallback component renders."""
    send(ws_event("tool-call", CHAT_ID, {
        "toolCallId": "tc_search_1",
        "toolName": "mcp__cortex__search",
        "args": {"query": "duck facts"},
        "argsText": '{"query": "duck facts"}'
    }))
    send(ws_event("tool-result", CHAT_ID, {
        "toolCallId": "tc_search_1",
        "result": "Found 3 memories about ducks",
        "isError": False
    }))
    send(ws_event("done", CHAT_ID))

    el = page.locator('[data-testid="tool-call"]')
    expect(el).to_be_visible(timeout=5000)
    page.wait_for_timeout(200)

    page.screenshot(path=str(screenshots / "test_tool_call.png"))

    # Root element classes (gap-based spacing — no individual margins)
    expect(el).to_have_class(re.compile(r"\brounded-lg\b"))
    expect(el).to_have_class(re.compile(r"\bborder\b"))
    expect(el).to_have_class(re.compile(r"\bborder-border\b"))
    expect(el).to_have_class(re.compile(r"\bbg-surface\b"))
    expect(el).to_have_class(re.compile(r"\boverflow-hidden\b"))

    # Button exists with classes
    button = el.locator("button")
    expect(button).to_be_visible()
    expect(button).to_have_class(re.compile(r"\bw-full\b"))
    expect(button).to_have_class(re.compile(r"\bflex\b"))
    expect(button).to_have_class(re.compile(r"\bitems-center\b"))
    expect(button).to_have_class(re.compile(r"\bgap-2\b"))
    expect(button).to_have_class(re.compile(r"\bpx-3\b"))
    expect(button).to_have_class(re.compile(r"\bfont-mono\b"))
    expect(button).to_have_class(re.compile(r"\btext-left\b"))

    # Status dot (first span inside button)
    status_dot = button.locator("span").first
    expect(status_dot).to_be_visible()
    expect(status_dot).to_have_class(re.compile(r"\bw-2\b"))
    expect(status_dot).to_have_class(re.compile(r"\bh-2\b"))
    expect(status_dot).to_have_class(re.compile(r"\brounded-full\b"))
    expect(status_dot).to_have_class(re.compile(r"\bbg-success\b"))

    # Status dot should NOT have animate-pulse-dot (streaming is done)
    dot_classes = status_dot.get_attribute("class")
    assert "animate-pulse-dot" not in dot_classes, "Status dot should not be animating after done"

    # Tool name span exists with classes
    tool_name_span = button.locator("span.text-primary")
    expect(tool_name_span).to_be_visible()
    expect(tool_name_span).to_have_class(re.compile(r"\btext-primary\b"))
    expect(tool_name_span).to_have_class(re.compile(r"\bfont-semibold\b"))

    # Tool name displays formatted name (strips mcp__ prefix)
    tool_name_text = tool_name_span.inner_text()
    assert "cortex" in tool_name_text.lower(), f"Expected 'cortex' in tool name, got: {tool_name_text}"
    assert "search" in tool_name_text.lower(), f"Expected 'search' in tool name, got: {tool_name_text}"
    assert "mcp__" not in tool_name_text, f"Tool name should not contain 'mcp__' prefix, got: {tool_name_text}"

    # Arrow span shows collapsed state
    arrow_span = button.locator("span").last
    expect(arrow_span).to_have_text("▶")

    # Exactly one tool-call on the page
    all_tool_calls = page.locator('[data-testid="tool-call"]')
    expect(all_tool_calls).to_have_count(1)

    # The tool call is inside an assistant-message container
    assistant_message = page.locator('[data-testid="assistant-message"]')
    expect(assistant_message).to_be_visible()
    tool_call_in_assistant = assistant_message.locator('[data-testid="tool-call"]')
    expect(tool_call_in_assistant).to_have_count(1)

    # No memory-note present (this is a search, not a store)
    memory_note = page.locator('[data-testid="memory-note"]')
    expect(memory_note).to_have_count(0)

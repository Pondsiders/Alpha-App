"""Golden path — the one test to rule them all.

New chat → click buzzer → receive response → send message → receive response.

Exercises both entry points (buzzer and regular send) through the complete
chain: Playwright → Chromium → built frontend → real backend → Claude
subprocess → mock Anthropic API → SSE → streaming back.

If this test passes, the app works end-to-end.
"""

import re
from pathlib import Path

from playwright.sync_api import Page, expect

SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
INPUT_SELECTOR = '[placeholder="Message Alpha..."]'
ASSISTANT_MSG_SELECTOR = ".group\\/assistant"
BUZZ_SELECTOR = '[data-testid="buzz-button"]'
MODEL_TIMEOUT = 15_000
NAV_TIMEOUT = 5_000


def _screenshot(page: Page, name: str) -> None:
    """Save a screenshot for debugging. We're headless — this is our eyes."""
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_DIR / f"{name}.png"), full_page=True)


def _enter_chat(page: Page, base_url: str) -> None:
    """Navigate to /chat and wait for auto-create to assign a chat ID."""
    page.goto(f"{base_url}/chat")
    page.wait_for_url(re.compile(r"/chat/.+"), timeout=NAV_TIMEOUT)


def test_golden_path(page: Page, base_url: str) -> None:
    """New chat → buzz → response → send → response.

    The complete happy path from fresh chat to conversation.
    The buzzer creates an assistant response without a user message —
    it's a stage direction, invisible to the human. Then a regular send
    creates a standard user→assistant exchange.

    Both paths exercise the full streaming pipeline: WebSocket → backend
    → enrobe → Claude subprocess → mock Anthropic → SSE → streaming back.
    """
    _enter_chat(page, base_url)

    # --- Step 1: Fresh chat, buzzer visible ---
    buzz_btn = page.locator(BUZZ_SELECTOR)
    expect(buzz_btn).to_be_visible(timeout=NAV_TIMEOUT)
    _screenshot(page, "golden_01_fresh_chat")

    # --- Step 2: Click the buzzer ---
    buzz_btn.click()

    # Assistant response should appear (mock returns lorem ipsum)
    first_msg = page.locator(ASSISTANT_MSG_SELECTOR).first
    expect(first_msg).to_be_visible(timeout=MODEL_TIMEOUT)
    expect(first_msg).not_to_be_empty()
    _screenshot(page, "golden_02_buzz_response")

    # Buzzer should disappear now (messages.length > 0)
    expect(buzz_btn).not_to_be_visible()

    # Human pause — let the turn fully complete, state settle
    page.wait_for_timeout(2_000)
    _screenshot(page, "golden_02b_pre_send")

    # --- Step 3: Send a regular message ---
    input_box = page.locator(INPUT_SELECTOR)
    expect(input_box).to_be_visible(timeout=NAV_TIMEOUT)

    input_box.fill("§echo:The duck quacks back")
    _screenshot(page, "golden_02c_filled")

    # Click the send button explicitly
    send_btn = page.locator('[data-testid="send-button"]')
    send_btn.click()

    _screenshot(page, "golden_02d_after_send")

    # Second assistant response with exact echo
    second_msg = page.locator(ASSISTANT_MSG_SELECTOR).nth(1)
    expect(second_msg).to_be_visible(timeout=MODEL_TIMEOUT)
    expect(second_msg).to_contain_text("The duck quacks back")
    _screenshot(page, "golden_03_conversation")

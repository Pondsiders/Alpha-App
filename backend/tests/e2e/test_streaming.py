"""End-to-end streaming tests — the tests that catch the bug we have right now.

These tests use Playwright to drive a real browser against the full stack:
uvicorn serving the built frontend + WebSocket + claude subprocess.

Run with:
    cd Alpha-App/backend
    uv run pytest tests/e2e/ -v

Prerequisites:
    - `vite build` in ../frontend (creates dist/ that uvicorn serves)
    - Redis running (for chat persistence)
    - ANTHROPIC_API_KEY set (for claude subprocess)
"""

import re
from pathlib import Path

from playwright.sync_api import Page, expect

# Screenshots go here for post-mortem diagnosis.
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"

# Selectors for the UI elements.
# ComposerPrimitive.Input renders a textarea with this placeholder.
INPUT_SELECTOR = '[placeholder="Message Alpha..."]'

# AssistantMessage root has this Tailwind group class.
# Using CSS attribute-contains selector for the group/assistant class.
ASSISTANT_MSG_SELECTOR = ".group\\/assistant"

# How long to wait for the model to respond. This is the one genuinely
# slow step — claude needs time to think. Everything else should be fast.
MODEL_TIMEOUT = 30_000

# How long to wait for UI navigation and element visibility.
NAV_TIMEOUT = 5_000


def _screenshot(page: Page, name: str) -> None:
    """Save a screenshot for debugging. We're headless — this is our eyes."""
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    path = SCREENSHOT_DIR / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)


def _enter_chat(page: Page, base_url: str) -> None:
    """Navigate to /chat and wait for auto-create to assign a chat ID.

    The app auto-creates a chat when you hit /chat with no ID, then
    navigates to /chat/{id}. We need to wait for that navigation before
    interacting, otherwise activeChatId is null and messages get dropped.
    """
    page.goto(f"{base_url}/chat")
    page.wait_for_url(re.compile(r"/chat/.+"), timeout=NAV_TIMEOUT)


def test_smoke_send_and_receive(page: Page, base_url: str) -> None:
    """Smoke test: send a message, verify assistant output appears.

    This is the most basic test. If this fails, nothing works.
    """
    _enter_chat(page, base_url)
    _screenshot(page, "01_in_chat")

    # Wait for the composer input
    input_box = page.locator(INPUT_SELECTOR)
    expect(input_box).to_be_visible(timeout=NAV_TIMEOUT)

    # Send a simple message
    input_box.fill("Say exactly: hello world")
    input_box.press("Enter")

    # Capture what we see right after sending
    _screenshot(page, "02_after_send")

    # Dump the page HTML so we can inspect the actual DOM
    html_path = SCREENSHOT_DIR / "page_after_send.html"
    html_path.write_text(page.content())

    # Wait for assistant output to appear
    assistant_msg = page.locator(ASSISTANT_MSG_SELECTOR).first
    expect(assistant_msg).to_be_visible(timeout=MODEL_TIMEOUT)
    expect(assistant_msg).not_to_be_empty()

    _screenshot(page, "03_response_visible")


def test_streaming_survives_backend_restart(
    page: Page, base_url: str, backend
) -> None:
    """THE test. Send a message, restart the backend, send another.

    This is the test that catches the reliability bug: after a backend
    restart, the WebSocket reconnects but streaming stops working.
    The browser shows no output from the model.

    If this test passes, the app is resilient to backend restarts.
    """
    _enter_chat(page, base_url)

    # --- First message: establish that streaming works ---
    input_box = page.locator(INPUT_SELECTOR)
    expect(input_box).to_be_visible(timeout=NAV_TIMEOUT)

    input_box.fill("Say exactly: before restart")
    input_box.press("Enter")

    # Verify output appears
    first_msg = page.locator(ASSISTANT_MSG_SELECTOR).first
    expect(first_msg).to_be_visible(timeout=MODEL_TIMEOUT)
    expect(first_msg).not_to_be_empty()

    # --- Kill and restart the backend ---
    backend.restart()

    # Wait for the WebSocket to reconnect.
    # The useWebSocket hook has exponential backoff: 1s, 2s, 4s, 8s, 16s.
    # Give it time to reconnect and be ready.
    page.wait_for_timeout(5_000)

    # --- Second message: this is where the bug lives ---
    # After restart, the old chat is dead (subprocess gone).
    # Navigate to /chat to trigger auto-create of a fresh chat.
    _enter_chat(page, base_url)

    input_box = page.locator(INPUT_SELECTOR)
    expect(input_box).to_be_visible(timeout=NAV_TIMEOUT)

    input_box.fill("Say exactly: after restart")
    input_box.press("Enter")

    # THIS IS THE ASSERTION THAT CURRENTLY FAILS
    second_msg = page.locator(ASSISTANT_MSG_SELECTOR).first
    expect(second_msg).to_be_visible(timeout=MODEL_TIMEOUT)
    expect(second_msg).not_to_be_empty()

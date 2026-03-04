"""End-to-end streaming tests — Playwright + Mock Anthropic API.

These tests use Playwright to drive a real browser against the full stack:
uvicorn serving the built frontend + WebSocket + Engine + claude subprocess
+ SDK proxy → mock Anthropic API. Everything real except the brain.

The mock API responds deterministically via §-commands. No real model
inference = fast, repeatable, no API key needed for the response content.

Run with:
    cd Alpha-App/frontend && npm run build
    cd Alpha-App/backend && uv run pytest tests/e2e/ -v

Prerequisites:
    - `npm run build` in frontend/ (creates dist/ that uvicorn serves)
    - Postgres running with app.chats table (for chat persistence)
    - DATABASE_URL set (connection string for Postgres)
    - ANTHROPIC_API_KEY set (claude subprocess needs it for auth headers,
      even though the actual API call goes to our mock)
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
ASSISTANT_MSG_SELECTOR = ".group\\/assistant"

# How long to wait for the model to respond. With the mock API this is
# fast — but we still need time for the full chain: WebSocket → Engine
# → claude subprocess → proxy → mock → response → streaming back.
MODEL_TIMEOUT = 15_000

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
    Uses the default mock response (lorem ipsum).
    """
    _enter_chat(page, base_url)
    _screenshot(page, "01_in_chat")

    # Wait for the composer input
    input_box = page.locator(INPUT_SELECTOR)
    expect(input_box).to_be_visible(timeout=NAV_TIMEOUT)

    # Send a message — no § prefix → lorem ipsum response
    input_box.fill("Hello, world!")
    input_box.press("Enter")

    _screenshot(page, "02_after_send")

    # Wait for assistant output to appear
    assistant_msg = page.locator(ASSISTANT_MSG_SELECTOR).first
    expect(assistant_msg).to_be_visible(timeout=MODEL_TIMEOUT)
    expect(assistant_msg).not_to_be_empty()

    _screenshot(page, "03_response_visible")


def test_echo_deterministic(page: Page, base_url: str) -> None:
    """§echo returns exact text — verifies the full streaming pipeline.

    If the echoed text appears in the DOM, the entire chain works:
    browser → WebSocket → backend → Engine → claude → proxy → mock
    → SSE → proxy → Engine → backend → WebSocket → browser.
    """
    _enter_chat(page, base_url)

    input_box = page.locator(INPUT_SELECTOR)
    expect(input_box).to_be_visible(timeout=NAV_TIMEOUT)

    # Send with §echo command — the mock will echo this exact text
    input_box.fill("§echo:The duck quacks at midnight")
    input_box.press("Enter")

    # The assistant message should contain our echoed text
    assistant_msg = page.locator(ASSISTANT_MSG_SELECTOR).first
    expect(assistant_msg).to_be_visible(timeout=MODEL_TIMEOUT)
    expect(assistant_msg).to_contain_text("The duck quacks at midnight")


def test_streaming_survives_backend_restart(
    page: Page, base_url: str, backend
) -> None:
    """THE test. Same window, no refresh, send after backend restart.

    This tests the actual failure mode Jeffery found: backend restarts,
    WebSocket reconnects, user sends a message to a chat whose subprocess
    is dead. The backend must load the chat from Postgres, resurrect it
    (new subprocess with --resume), and stream the response.

    NO page refresh. NO navigation. Same window, same chat. If this
    passes, the full resurrection path works end-to-end.
    """
    _enter_chat(page, base_url)

    # --- First message: establish that streaming works ---
    input_box = page.locator(INPUT_SELECTOR)
    expect(input_box).to_be_visible(timeout=NAV_TIMEOUT)

    input_box.fill("§echo:Before the storm")
    input_box.press("Enter")

    first_msg = page.locator(ASSISTANT_MSG_SELECTOR).first
    expect(first_msg).to_be_visible(timeout=MODEL_TIMEOUT)
    expect(first_msg).to_contain_text("Before the storm")

    _screenshot(page, "04_before_restart")

    # --- Kill and restart the backend ---
    backend.restart()

    # Wait for the WebSocket to reconnect.
    # useWebSocket has exponential backoff: 1s, 2s, 4s, 8s.
    # Give it plenty of time — reconnection is not the part we're testing.
    page.wait_for_timeout(8_000)

    _screenshot(page, "05_after_restart")

    # --- Second message: same window, same chat, no navigation ---
    # The old chat's subprocess is dead. The backend must:
    # 1. Load chat metadata from Postgres (DEAD, has session_uuid)
    # 2. Resurrect (start new subprocess, --resume session)
    # 3. Send the message
    # 4. Stream the response back through the new WebSocket
    input_box = page.locator(INPUT_SELECTOR)
    expect(input_box).to_be_visible(timeout=NAV_TIMEOUT)

    input_box.fill("§echo:After the storm")
    input_box.press("Enter")

    # THIS IS THE ASSERTION THAT MATTERS.
    # Wait for the SECOND assistant message — the first ("Before the storm")
    # is still in the DOM from before the restart.
    # Generous timeout: resurrection = subprocess startup + session resume + drain.
    RESTART_TIMEOUT = 30_000
    second_msg = page.locator(ASSISTANT_MSG_SELECTOR).nth(1)
    expect(second_msg).to_be_visible(timeout=RESTART_TIMEOUT)
    expect(second_msg).to_contain_text("After the storm")

    _screenshot(page, "06_survived_restart")

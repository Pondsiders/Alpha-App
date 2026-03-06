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


def test_chat_switch_streaming(page: Page, base_url: str) -> None:
    """Chat switch via client-side navigation (WebSocket stays connected).

    Tests the EASY case: React Router internal navigation. The WebSocket
    stays open, Zustand state persists, messages are cached in memory.
    This passes. It's not the bug Jeffery found.

    See test_chat_switch_after_browser_close for the REAL bug.
    """
    # --- Step 1: Visit root URL, land in chat A ---
    page.goto(f"{base_url}/")
    page.wait_for_url(re.compile(r"/chat/.+"), timeout=NAV_TIMEOUT)
    chat_a_url = page.url

    input_box = page.locator(INPUT_SELECTOR)
    expect(input_box).to_be_visible(timeout=NAV_TIMEOUT)

    _screenshot(page, "07_chat_a_ready")

    # --- Step 2: Send message in chat A ---
    input_box.fill("§echo:Alpha remembers")
    input_box.press("Enter")

    first_msg = page.locator(ASSISTANT_MSG_SELECTOR).first
    expect(first_msg).to_be_visible(timeout=MODEL_TIMEOUT)
    expect(first_msg).to_contain_text("Alpha remembers")

    _screenshot(page, "08_chat_a_response")

    # Human pause — Jeffery isn't a robot
    page.wait_for_timeout(2_000)

    # --- Step 3: Click "New chat" (client-side navigation, no page reload) ---
    # This is different from page.goto() — React Router navigates internally,
    # WebSocket stays connected, state persists. Tests the real user flow.
    sidebar = page.locator("[data-sidebar='sidebar']")
    new_chat_btn = sidebar.locator("button").filter(has_text="New chat")
    expect(new_chat_btn).to_be_visible(timeout=NAV_TIMEOUT)
    new_chat_btn.click()
    page.wait_for_url(re.compile(r"/chat/.+"), timeout=NAV_TIMEOUT)
    assert page.url != chat_a_url, "Should be on a new chat, not chat A"

    _screenshot(page, "09_on_chat_b")

    # Human pause — let the page settle
    page.wait_for_timeout(2_000)

    # --- Step 4: Click back to chat A in the sidebar ---
    sidebar = page.locator("[data-sidebar='sidebar']")
    chat_a_button = sidebar.locator("button").filter(has_text="Alpha remembers")
    expect(chat_a_button).to_be_visible(timeout=NAV_TIMEOUT)
    chat_a_button.click()

    # Wait for navigation back to chat A
    page.wait_for_url(chat_a_url, timeout=NAV_TIMEOUT)

    _screenshot(page, "10_back_on_chat_a")

    # Human pause — look at the chat, then type
    page.wait_for_timeout(2_000)

    # --- Step 5: Send another message in chat A ---
    input_box = page.locator(INPUT_SELECTOR)
    expect(input_box).to_be_visible(timeout=NAV_TIMEOUT)

    input_box.fill("§echo:Still here after switch")
    input_box.press("Enter")

    # THIS IS THE ASSERTION THAT MATTERS.
    # The response must stream and render without a page refresh.
    second_msg = page.locator(ASSISTANT_MSG_SELECTOR).nth(1)
    expect(second_msg).to_be_visible(timeout=MODEL_TIMEOUT)
    expect(second_msg).to_contain_text("Still here after switch")

    _screenshot(page, "11_chat_switch_survived")


def test_chat_switch_after_browser_close(
    page: Page, base_url: str, backend
) -> None:
    """THE bug. Streaming breaks after browser close + backend restart.

    Reproduces Jeffery's exact steps (Mar 4, 2026):
    1. Open browser, go to root URL
    2. New chat auto-created, send message, see response ✓
    3. Quit browser
    4. Restart Alpha-App
    5. Open browser, paste in root URL
    6. Select PREVIOUS chat from sidebar
    7. Type message, hit enter
    8. Response fails to stream in

    BOTH conditions matter:
    - Browser close = empty Zustand, fresh WebSocket, no JS state
    - Backend restart = all chats DEAD, must resurrect from Postgres

    The browser-close-only variant passes. The backend-restart-only
    variant passes. It's the COMBINATION that breaks.

    No §-commands. Normal messages, lorem ipsum responses. The assertion
    is just "did an assistant response appear?" — same as what Jeffery
    checks by eyeball.

    Human-speed pauses between steps. This isn't a timing test.
    """
    # --- Steps 1-2: First browser session — create chat, send, verify ---
    page.goto(f"{base_url}/")
    page.wait_for_url(re.compile(r"/chat/.+"), timeout=NAV_TIMEOUT)

    input_box = page.locator(INPUT_SELECTOR)
    expect(input_box).to_be_visible(timeout=NAV_TIMEOUT)

    # Normal message. No § magic. Just a person talking.
    input_box.fill("Tell me about rubber ducks")
    input_box.press("Enter")

    # The mock returns lorem ipsum. We just need to see it appear.
    first_response = page.locator(ASSISTANT_MSG_SELECTOR).first
    expect(first_response).to_be_visible(timeout=MODEL_TIMEOUT)
    expect(first_response).not_to_be_empty()

    _screenshot(page, "12_first_session_response")

    # Human pause — Jeffery reads the response, then closes Chrome
    page.wait_for_timeout(2_000)

    # --- Step 3: QUIT BROWSER ---
    browser = page.context.browser
    page.context.close()  # Closes page AND context — nuclear option

    # --- Step 4: RESTART ALPHA-APP ---
    # This is the key step the previous test was missing. After restart,
    # all in-memory Chat objects are gone. The holster warms a fresh
    # subprocess. Existing chats must be loaded from Postgres and
    # resurrected via --resume.
    backend.restart()

    # --- Step 5: OPEN BROWSER, GO TO ROOT URL ---
    new_context = browser.new_context()
    page2 = new_context.new_page()
    page2.goto(f"{base_url}/")

    # Root URL auto-creates a new chat (chat B) and redirects.
    # Chat A should appear in the sidebar, loaded from Postgres.
    page2.wait_for_url(re.compile(r"/chat/.+"), timeout=NAV_TIMEOUT)

    _screenshot(page2, "13_fresh_browser_after_restart")

    # Human pause — the page loads, sidebar populates
    page2.wait_for_timeout(2_000)

    # --- Step 6: Select PREVIOUS chat from sidebar ---
    sidebar = page2.locator("[data-sidebar='sidebar']")
    previous_chat = sidebar.locator("button").filter(has_text="Tell me about rubber ducks")
    expect(previous_chat).to_be_visible(timeout=NAV_TIMEOUT)
    previous_chat.click()

    _screenshot(page2, "14_selected_previous_chat")

    # Human pause — messages load from the backend, Jeffery reads, then types
    page2.wait_for_timeout(2_000)

    # --- Steps 7-8: Send message, observe response stream ---
    input_box2 = page2.locator(INPUT_SELECTOR)
    expect(input_box2).to_be_visible(timeout=NAV_TIMEOUT)

    # Another normal message. Jeffery is just talking.
    input_box2.fill("Now translate that into French")
    input_box2.press("Enter")

    # THIS IS THE ASSERTION THAT MATTERS.
    # There should be TWO assistant messages: the first loaded from history,
    # the second just streamed in. We check the second one exists at all.
    # If it doesn't appear, the streaming pipeline broke — exactly the bug.
    #
    # Generous timeout: resurrection = subprocess start + resume + drain + turn.
    RESURRECTION_TIMEOUT = 30_000
    second_response = page2.locator(ASSISTANT_MSG_SELECTOR).nth(1)
    expect(second_response).to_be_visible(timeout=RESURRECTION_TIMEOUT)
    expect(second_response).not_to_be_empty()

    _screenshot(page2, "15_survived_browser_close_and_restart")

    # Cleanup
    new_context.close()


def test_interjection_during_streaming(page: Page, base_url: str) -> None:
    """Duplex: send a message while the assistant is still streaming.

    Tests the full-duplex UI:
    1. Both send and stop buttons visible during streaming
    2. User can type and send while assistant is streaming
    3. Interjection appears as a user bubble below the streaming message
    4. No errors — WebSocket stays alive, chat survives

    Uses §slow for a deliberately slow response (~9 seconds of streaming)
    that gives plenty of time to send the interjection.
    """
    _enter_chat(page, base_url)

    input_box = page.locator(INPUT_SELECTOR)
    expect(input_box).to_be_visible(timeout=NAV_TIMEOUT)

    # Send §slow — 200ms between chunks, ~9 seconds of streaming
    input_box.fill("§slow")
    input_box.press("Enter")

    # Wait for streaming to start (assistant bubble appears with text)
    assistant_msg = page.locator(ASSISTANT_MSG_SELECTOR).first
    expect(assistant_msg).to_be_visible(timeout=MODEL_TIMEOUT)
    expect(assistant_msg).to_contain_text("Lorem", timeout=MODEL_TIMEOUT)

    _screenshot(page, "16_streaming_started")

    # Verify stop button is visible during streaming (duplex UI)
    stop_btn = page.locator('[data-testid="stop-button"]')
    expect(stop_btn).to_be_visible(timeout=NAV_TIMEOUT)

    # INTERJECTION: send while the assistant is still streaming.
    # The composer should still be accessible because we set
    # isRunning=false in the runtime for duplex support.
    input_box = page.locator(INPUT_SELECTOR)
    expect(input_box).to_be_visible(timeout=NAV_TIMEOUT)
    input_box.fill("Wait, also tell me about ducks!")
    input_box.press("Enter")

    _screenshot(page, "17_interjection_sent")

    # The interjection should appear as a user bubble BELOW the
    # still-streaming assistant message. This is the key assertion:
    # the thread reads chronologically — user, assistant (streaming),
    # user (interjection).
    user_bubbles = page.locator(".bg-user-bubble")
    expect(user_bubbles.nth(1)).to_be_visible(timeout=NAV_TIMEOUT)
    expect(user_bubbles.nth(1)).to_contain_text("also tell me about ducks")

    # The assistant message should still be present (streaming or done)
    expect(assistant_msg).to_be_visible()

    _screenshot(page, "18_interjection_visible")

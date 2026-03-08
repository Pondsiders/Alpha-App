"""Approach light e2e test — four-beat scripted play.

Tests the full approach light pipeline end-to-end:
  Browser → WebSocket → Backend → Engine → claude → proxy → mock API
  → SSE with custom input_tokens → proxy sniffs → Chat.check_approach_threshold()
  → streaming.py broadcasts approach-light event + sends interjection
  → drain loop consumes interjection response → frontend renders annotation

Four scripted beats, each with a specific token count:
  Beat 1: §test_approach_lights_1 — 75k tokens (37.5%). Below yellow. Silence.
  Beat 2: §test_approach_lights_2 — 135k tokens (67.5%). Crosses yellow. Amber.
  Beat 3: §test_approach_lights_3 — 155k tokens (77.5%). Crosses red. Red.
  Beat 4: §test_approach_lights_4 — 170k tokens (85%). No new threshold. Fin.

The approach lights use the one-shot pattern: each threshold fires exactly
once per session. After beats 2 and 3, the interjection response is drained
by streaming.py so the pipe is clean for the next beat.

Designed by Jeffery. The interlocutor and Mr. Bones.

Run with:
    cd Alpha-App/frontend && npm run build
    cd Alpha-App/backend && uv run pytest tests/e2e/test_approach_lights.py -v
"""

from pathlib import Path

from playwright.sync_api import Page, expect

SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
INPUT_SELECTOR = '[placeholder="Message Alpha..."]'
ASSISTANT_MSG_SELECTOR = ".group\\/assistant"
MODEL_TIMEOUT = 30_000
NAV_TIMEOUT = 5_000


def _screenshot(page: Page, name: str) -> None:
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_DIR / f"{name}.png"), full_page=True)


def _enter_chat(page: Page, base_url: str) -> None:
    import re
    page.goto(f"{base_url}/chat")
    page.wait_for_url(re.compile(r"/chat/.+"), timeout=NAV_TIMEOUT)


def _send_and_wait(page: Page, text: str, nth: int) -> None:
    """Type a message, send it, and wait for the Nth assistant response."""
    input_box = page.locator(INPUT_SELECTOR)
    input_box.fill(text)
    input_box.press("Enter")

    assistant = page.locator(ASSISTANT_MSG_SELECTOR).nth(nth)
    expect(assistant).to_be_visible(timeout=MODEL_TIMEOUT)
    expect(assistant).not_to_be_empty()

    # Give events time to propagate (approach light broadcasts, drain)
    page.wait_for_timeout(2_000)


def test_approach_lights(page: Page, base_url: str) -> None:
    """Four beats, four assertions. The negative case comes first.

    Beat 1: 37.5% — silence. No approach light.
    Beat 2: 67.5% — yellow fires. Amber annotation appears.
    Beat 3: 77.5% — red fires. Red annotation appears.
    Beat 4: 85.0% — no new threshold. Both lights persist. Chat works. Fin.
    """
    _enter_chat(page, base_url)

    input_box = page.locator(INPUT_SELECTOR)
    expect(input_box).to_be_visible(timeout=NAV_TIMEOUT)

    yellow = page.locator('[data-testid="approach-light-yellow"]')
    red = page.locator('[data-testid="approach-light-red"]')

    # -- Beat 1: 37.5% — below yellow threshold, no alert --
    _send_and_wait(page, "§test_approach_lights_1", nth=0)

    expect(yellow).to_have_count(0)
    expect(red).to_have_count(0)

    _screenshot(page, "approach_01_silence")

    # -- Beat 2: 67.5% — crosses yellow threshold --
    _send_and_wait(page, "§test_approach_lights_2", nth=1)

    expect(yellow).to_be_visible(timeout=NAV_TIMEOUT)
    expect(yellow).to_contain_text("65%")
    expect(red).to_have_count(0)

    _screenshot(page, "approach_02_yellow")

    # -- Beat 3: 77.5% — crosses red threshold --
    _send_and_wait(page, "§test_approach_lights_3", nth=2)

    expect(red).to_be_visible(timeout=NAV_TIMEOUT)
    expect(red).to_contain_text("75%")

    # Yellow persists — one-shot, never removed
    expect(yellow).to_have_count(1)

    _screenshot(page, "approach_03_red")

    # -- Beat 4: 85% — no new threshold, verify chat still works --
    _send_and_wait(page, "§test_approach_lights_4", nth=3)

    # Both lights still visible
    expect(yellow).to_have_count(1)
    expect(red).to_have_count(1)

    _screenshot(page, "approach_04_fin")

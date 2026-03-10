"""Frontend isolation test fixtures — no backend, mock WebSocket.

Serves the built frontend from a simple HTTP server and uses Playwright's
routeWebSocket() to intercept the WebSocket connection. Events are fed
directly into the browser — the frontend thinks it's talking to the backend.

Run `cd frontend && npm run build` before running these tests.

Options:
    --headed    Run with a visible browser window
"""

import http.server
import json
import shutil
import threading
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


# The built frontend lives here after `npm run build`
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

# Screenshots for post-mortem diagnosis
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"

# Port for the static file server
STATIC_PORT = 18097

# Chat ID used across all tests
CHAT_ID = "test-chat"


# -- CLI options --------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--headed", action="store_true", default=False,
        help="Run browser in headed mode (visible window)",
    )


# -- Static file server -------------------------------------------------------

class _StaticHandler(http.server.SimpleHTTPRequestHandler):
    """Serve the built frontend. SPA fallback: serve index.html for all routes."""

    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIST), **kwargs)

    def do_GET(self):
        path = FRONTEND_DIST / self.path.lstrip("/")
        if not path.exists() and not path.suffix:
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, format, *args):
        pass


# -- Helpers ------------------------------------------------------------------

def ws_event(event_type: str, chat_id: str | None = None, data=None) -> str:
    """Build a JSON WebSocket event string, matching ServerEvent shape."""
    msg: dict = {"type": event_type}
    if chat_id is not None:
        msg["chatId"] = chat_id
    if data is not None:
        msg["data"] = data
    return json.dumps(msg)


# -- Fixtures -----------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _clean_screenshots():
    """Wipe screenshots at session start so stale images don't mislead."""
    if SCREENSHOT_DIR.exists():
        shutil.rmtree(SCREENSHOT_DIR)
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    yield


@pytest.fixture(scope="session")
def frontend_url():
    """Start a static file server for the built frontend."""
    if not FRONTEND_DIST.exists():
        pytest.skip("Frontend not built — run `cd frontend && npm run build` first")

    server = http.server.HTTPServer(("127.0.0.1", STATIC_PORT), _StaticHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{STATIC_PORT}"
    server.shutdown()


@pytest.fixture(scope="session")
def _browser(request):
    """Chromium instance shared across all tests. Launched once."""
    headed = request.config.getoption("--headed")
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=not headed)
    yield browser
    browser.close()
    pw.stop()


@pytest.fixture()
def page(_browser, frontend_url):
    """Fresh browser page per test, connected to the app with WS mock.

    Each test gets a clean page — no accumulated state from other tests.
    The WS handshake (list-chats → chat-list, create-chat → chat-created)
    is handled automatically. Tests send their own events via `send`.
    """
    context = _browser.new_context(viewport={"width": 1280, "height": 800})
    _page = context.new_page()

    route_holder = []

    def handle_ws(route):
        route_holder.append(route)

        def on_message(message):
            try:
                parsed = json.loads(message)
            except (json.JSONDecodeError, TypeError):
                return

            msg_type = parsed.get("type")

            if msg_type == "list-chats":
                route.send(ws_event("chat-list", data=[{
                    "chatId": CHAT_ID,
                    "title": "Test Chat",
                    "state": "dead",
                    "updatedAt": 1710000000,
                }]))

            elif msg_type == "create-chat":
                route.send(json.dumps({
                    "type": "chat-created",
                    "chatId": CHAT_ID,
                    "data": {"state": "dead"},
                }))

        route.on_message(on_message)

    _page.route_web_socket("**/ws", handle_ws)
    _page.goto(f"{frontend_url}/chat")

    # Wait for the handshake to complete (chat-created → redirect)
    _page.wait_for_url(f"**/chat/{CHAT_ID}", timeout=10000)

    # Stash the route on the page object so the send fixture can find it
    _page._ws_route = route_holder[0]

    yield _page

    context.close()


@pytest.fixture()
def send(page):
    """Callable that sends a raw WS event string to the page."""
    route = page._ws_route
    def _send(event: str):
        route.send(event)
    return _send


@pytest.fixture(scope="session")
def screenshots():
    return SCREENSHOT_DIR

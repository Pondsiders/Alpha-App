"""End-to-end test fixtures — mock API + backend lifecycle + Playwright.

Three-layer test stack:
  1. Mock Anthropic API  (port 18098) — deterministic SSE responses
  2. Alpha-App backend   (port 18099) — uvicorn serving the built frontend
  3. Playwright browser   — headless Chrome driving the real UI

The backend starts with ANTHROPIC_BASE_URL pointed at the mock. The full
chain runs for real — browser, WebSocket, FastAPI, Engine, claude subprocess,
SDK proxy — except the API call at the very end hits the mock instead of
api.anthropic.com. "We trust Anthropic." We're testing our plumbing.

All tests run against a BUILT frontend served by uvicorn — no Vite dev
server. Run `vite build` in frontend/ before running these tests.
This matches production: one process, one port, one reality.
"""

import os
import signal
import subprocess
import sys
import time

import pytest
import requests

from tests.e2e.mock_anthropic import MockAnthropicServer


# -- Ports (all different so nothing collides) --------------------------------
MOCK_PORT = 18098   # Mock Anthropic API
E2E_PORT = 18099    # Alpha-App backend
E2E_BASE_URL = f"http://localhost:{E2E_PORT}"


# -- Backend subprocess manager -----------------------------------------------


class Backend:
    """Manages a uvicorn subprocess for testing."""

    def __init__(self, *, mock_api_url: str) -> None:
        self.port = E2E_PORT
        self.base_url = E2E_BASE_URL
        self._mock_api_url = mock_api_url
        self._proc: subprocess.Popen | None = None

    def start(self, *, timeout: float = 30.0) -> None:
        """Start the backend and wait until healthy."""
        env = {
            **os.environ,
            "PORT": str(self.port),
            # Route API requests to the mock instead of Anthropic.
            # The Engine captures this before overriding it for claude.
            "ANTHROPIC_BASE_URL": self._mock_api_url,
        }

        self._proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn",
                "alpha_app.main:app",
                "--host", "127.0.0.1",
                "--port", str(self.port),
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        # Poll /health until it responds
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                r = requests.get(f"{self.base_url}/health", timeout=2)
                if r.status_code == 200:
                    return
            except requests.ConnectionError:
                pass
            time.sleep(0.5)

        # If we get here, startup timed out
        self.stop()
        raise TimeoutError(
            f"Backend did not become healthy within {timeout}s on port {self.port}"
        )

    def stop(self) -> None:
        """Stop the backend gracefully, then force-kill if needed."""
        if self._proc is None:
            return

        try:
            self._proc.send_signal(signal.SIGTERM)
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        finally:
            self._proc = None

    def restart(self, *, timeout: float = 30.0) -> None:
        """Stop and restart the backend. This is the core of the survival test."""
        self.stop()
        time.sleep(1)  # Brief pause for port release
        self.start(timeout=timeout)

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None


# -- Fixtures -----------------------------------------------------------------


@pytest.fixture(scope="session")
def mock_api():
    """Session-scoped mock Anthropic API. Starts once, shared across all tests."""
    server = MockAnthropicServer(port=MOCK_PORT)
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def backend(mock_api: MockAnthropicServer):
    """Session-scoped backend. Starts after mock API, shared across all tests.

    The backend is configured with ANTHROPIC_BASE_URL pointing at the mock,
    so all API calls go to deterministic fixture responses.
    """
    b = Backend(mock_api_url=mock_api.base_url)
    b.start()
    yield b
    b.stop()


@pytest.fixture(scope="session")
def base_url(backend: Backend) -> str:
    """Base URL for Playwright — points at the backend."""
    return backend.base_url


# -- Playwright configuration ------------------------------------------------
# pytest-playwright reads `--base-url` from the CLI or this fixture.
# Running headless by default (no display needed).


@pytest.fixture(scope="session")
def browser_type_launch_args():
    """Playwright browser launch args — headless, no sandbox for CI."""
    return {"headless": True}

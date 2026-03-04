"""End-to-end test fixtures — backend lifecycle + Playwright.

The key fixture is `backend`, which starts uvicorn in a subprocess,
waits for it to be healthy, and provides .restart() for the
backend-restart survival test.

All tests in this directory run against a BUILT frontend served by
uvicorn — no Vite dev server. Run `vite build` before these tests.
This matches production: one process, one port, one reality.
"""

import os
import signal
import subprocess
import sys
import time

import pytest
import requests


# The port used exclusively for e2e tests. Different from dev (18010)
# so tests can run without conflicting with a running dev server.
E2E_PORT = 18099
E2E_BASE_URL = f"http://localhost:{E2E_PORT}"


class Backend:
    """Manages a uvicorn subprocess for testing."""

    def __init__(self) -> None:
        self.port = E2E_PORT
        self.base_url = E2E_BASE_URL
        self._proc: subprocess.Popen | None = None

    def start(self, *, timeout: float = 30.0) -> None:
        """Start the backend and wait until healthy."""
        env = {**os.environ, "PORT": str(self.port)}

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


@pytest.fixture(scope="session")
def backend():
    """Session-scoped backend fixture. Starts once, shared across all tests."""
    b = Backend()
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

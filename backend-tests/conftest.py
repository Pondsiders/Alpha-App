"""Backend protocol test fixtures — WebSocket puppeteering, no browser.

Starts MockAnthropic + the Alpha-App backend, then tests connect via
a raw websockets client to validate the WebSocket protocol: send events,
check what comes back. Everything real except the Anthropic API.

Run `cd frontend && npm run build` before running — the backend serves
the built frontend and won't start without it.
"""

import os
import shutil
import subprocess
import signal
import sys
import time
from pathlib import Path

import pytest
import requests

from mock_anthropic import MockAnthropicServer

# Screenshots directory (for any future visual debugging)
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"

# -- Ports (all different so nothing collides) --------------------------------
MOCK_PORT = 18098   # Mock Anthropic API
BACKEND_PORT = 18099  # Alpha-App backend
BACKEND_BASE_URL = f"http://localhost:{BACKEND_PORT}"
BACKEND_WS_URL = f"ws://localhost:{BACKEND_PORT}/ws"

# -- Test database ------------------------------------------------------------
TEST_DB_NAME = "alpha_test"


def _prod_database_url() -> str:
    """Get the production DATABASE_URL (points at the postgres database)."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set — can't create test database")
    return url


def _test_database_url() -> str:
    """Derive the test DATABASE_URL by swapping the database name."""
    prod_url = _prod_database_url()
    base, _, _ = prod_url.rpartition("/")
    return f"{base}/{TEST_DB_NAME}"


def _create_test_database() -> None:
    """Create the test database and set up the schema."""
    prod_url = _prod_database_url()

    subprocess.run(
        ["psql", prod_url, "-c",
         f"DROP DATABASE IF EXISTS {TEST_DB_NAME}"],
        capture_output=True, text=True,
    )

    result = subprocess.run(
        ["psql", prod_url, "-c",
         f"CREATE DATABASE {TEST_DB_NAME}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create test database: {result.stderr.strip()}")

    test_url = _test_database_url()
    sql = (
        "CREATE SCHEMA IF NOT EXISTS app; "
        "CREATE TABLE IF NOT EXISTS app.chats ("
        "  id TEXT PRIMARY KEY,"
        "  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
        "  data JSONB NOT NULL DEFAULT '{}'"
        "); "
        "CREATE INDEX IF NOT EXISTS idx_chats_updated_at "
        "  ON app.chats (updated_at DESC); "
        "CREATE TABLE IF NOT EXISTS app.events ("
        "  id BIGSERIAL PRIMARY KEY,"
        "  chat_id TEXT NOT NULL,"
        "  ts TIMESTAMPTZ NOT NULL DEFAULT now(),"
        "  event JSONB NOT NULL,"
        "  seq INTEGER"
        "); "
        "CREATE INDEX IF NOT EXISTS idx_events_chat_seq "
        "  ON app.events (chat_id, seq);"
    )
    result = subprocess.run(
        ["psql", test_url, "-c", sql],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to set up test schema: {result.stderr.strip()}")

    print(f"Test database '{TEST_DB_NAME}' created and ready")


def _drop_test_database() -> None:
    """Drop the test database."""
    prod_url = _prod_database_url()
    result = subprocess.run(
        ["psql", prod_url, "-c",
         f"DROP DATABASE IF EXISTS {TEST_DB_NAME}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"Warning: failed to drop test database: {result.stderr.strip()}")
    else:
        print(f"Test database '{TEST_DB_NAME}' dropped")


# -- Backend subprocess manager -----------------------------------------------

class Backend:
    """Manages a uvicorn subprocess for testing."""

    def __init__(self, *, mock_api_url: str, test_db_url: str) -> None:
        self.port = BACKEND_PORT
        self.base_url = BACKEND_BASE_URL
        self.ws_url = BACKEND_WS_URL
        self._mock_api_url = mock_api_url
        self._test_db_url = test_db_url
        self._proc: subprocess.Popen | None = None

    def start(self, *, timeout: float = 30.0) -> None:
        """Start the backend and wait until healthy."""
        env = {
            **os.environ,
            "PORT": str(self.port),
            "ANTHROPIC_BASE_URL": self._mock_api_url,
            "DATABASE_URL": self._test_db_url,
            "_ALPHA_REAP_TIMEOUT": "3",
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

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                r = requests.get(f"{self.base_url}/health", timeout=2)
                if r.status_code == 200:
                    return
            except requests.ConnectionError:
                pass
            time.sleep(0.5)

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
        """Stop and restart the backend."""
        self.stop()
        time.sleep(1)
        self.start(timeout=timeout)

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None


# -- Fixtures -----------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _test_db():
    """Create and destroy the test database."""
    _create_test_database()
    yield
    _drop_test_database()


@pytest.fixture(scope="session")
def mock_api():
    """Session-scoped mock Anthropic API."""
    server = MockAnthropicServer(port=MOCK_PORT)
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def backend(mock_api: MockAnthropicServer, _test_db):
    """Session-scoped backend, pointed at mock API and test database."""
    b = Backend(
        mock_api_url=mock_api.base_url,
        test_db_url=_test_database_url(),
    )
    b.start()
    yield b
    b.stop()


@pytest.fixture(scope="session")
def ws_url(backend: Backend) -> str:
    """WebSocket URL for protocol tests."""
    return backend.ws_url

"""End-to-end test fixtures — mock API + backend lifecycle + Playwright.

Three-layer test stack:
  1. Mock Anthropic API  (port 18098) — deterministic SSE responses
  2. Alpha-App backend   (port 18099) — uvicorn serving the built frontend
  3. Playwright browser   — headless Chrome driving the real UI

The backend starts with ANTHROPIC_BASE_URL pointed at the mock. The full
chain runs for real — browser, WebSocket, FastAPI, Postgres, Engine, claude
subprocess, SDK proxy — except the API call at the very end hits the mock
instead of api.anthropic.com. "We trust Anthropic." We're testing our plumbing.

Test isolation: tests run against a TEMPORARY DATABASE (alpha_test) created
at session start and dropped at session end. The production database is
never touched. Not even a little. Precious cargo.

All tests run against a BUILT frontend served by uvicorn — no Vite dev
server. Run `vite build` in frontend/ before running these tests.
This matches production: one process, one port, one reality.
"""

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

from tests.e2e.mock_anthropic import MockAnthropicServer

# Screenshots directory — wiped at session start so stale images don't mislead.
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"

# -- Ports (all different so nothing collides) --------------------------------
MOCK_PORT = 18098   # Mock Anthropic API
E2E_PORT = 18099    # Alpha-App backend
E2E_BASE_URL = f"http://localhost:{E2E_PORT}"

# -- Test database ------------------------------------------------------------
# Tests use a completely separate database. The production database (postgres)
# is never connected to by the backend under test.
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
    # URL format: postgresql://user:pass@host:port/dbname
    base, _, _ = prod_url.rpartition("/")
    return f"{base}/{TEST_DB_NAME}"


def _create_test_database() -> None:
    """Create the test database and set up the schema.

    Connects to the production database (postgres) to issue CREATE DATABASE,
    then connects to the new test database to create the app schema + table.
    Safe to call repeatedly — uses IF NOT EXISTS everywhere.
    """
    prod_url = _prod_database_url()

    # Step 1: Create the database (connecting to postgres)
    # DROP first in case a previous run left it behind (e.g. test crash)
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

    # Step 2: Create schema + table (connecting to alpha_test)
    test_url = _test_database_url()
    sql = (
        "CREATE SCHEMA IF NOT EXISTS app; "
        "CREATE TABLE IF NOT EXISTS app.chats ("
        "  id TEXT PRIMARY KEY,"
        "  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
        "  data JSONB NOT NULL DEFAULT '{}'"
        "); "
        "CREATE INDEX IF NOT EXISTS idx_chats_updated_at "
        "  ON app.chats (updated_at DESC);"
    )
    result = subprocess.run(
        ["psql", test_url, "-c", sql],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to set up test schema: {result.stderr.strip()}")

    print(f"Test database '{TEST_DB_NAME}' created and ready")


def _drop_test_database() -> None:
    """Drop the test database. Connects to postgres to issue the DROP."""
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
        self.port = E2E_PORT
        self.base_url = E2E_BASE_URL
        self._mock_api_url = mock_api_url
        self._test_db_url = test_db_url
        self._proc: subprocess.Popen | None = None

    def start(self, *, timeout: float = 30.0) -> None:
        """Start the backend and wait until healthy."""
        env = {
            **os.environ,
            "PORT": str(self.port),
            # Route API requests to the mock instead of Anthropic.
            # The Engine captures this before overriding it for claude.
            "ANTHROPIC_BASE_URL": self._mock_api_url,
            # Point at the test database — never touch production.
            "DATABASE_URL": self._test_db_url,
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


@pytest.fixture(scope="session", autouse=True)
def _clean_screenshots():
    """Wipe screenshots at session start so stale images don't mislead.

    Tests take screenshots at key moments for post-mortem diagnosis.
    Old screenshots from a previous run could confuse investigation if
    the current test fails before reaching a screenshot call.
    """
    if SCREENSHOT_DIR.exists():
        shutil.rmtree(SCREENSHOT_DIR)
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    yield


@pytest.fixture(scope="session", autouse=True)
def _test_db():
    """Create and destroy the test database.

    Creates alpha_test at session start, drops it at session end.
    The production database is never touched.
    """
    _create_test_database()
    yield
    _drop_test_database()


@pytest.fixture(scope="session")
def mock_api():
    """Session-scoped mock Anthropic API. Starts once, shared across all tests."""
    server = MockAnthropicServer(port=MOCK_PORT)
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def backend(mock_api: MockAnthropicServer, _test_db):
    """Session-scoped backend. Starts after mock API, shared across all tests.

    The backend is configured with ANTHROPIC_BASE_URL pointing at the mock
    and DATABASE_URL pointing at the test database, so tests are fully isolated.
    """
    b = Backend(
        mock_api_url=mock_api.base_url,
        test_db_url=_test_database_url(),
    )
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
def browser_type_launch_args(request):
    """Playwright browser launch args — respects --headed flag from CLI."""
    # pytest-playwright sets this based on --headed flag
    headed = request.config.getoption("--headed", default=False)
    return {"headless": not headed}

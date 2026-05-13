"""Fixtures for the e2e suite.

Each test gets a fresh database (via the function-scoped `test_db`
fixture from the parent conftest) and a freshly-spawned uvicorn
serving the built frontend on a random local port. Playwright's
`page` fixture drives a browser against the URL the `backend`
fixture yields.
"""

import os
import socket
import subprocess
import time
from collections.abc import Generator
from pathlib import Path

import httpx
import pytest

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent
_FRONTEND_DIST = _REPO_ROOT / "frontend" / "dist"


def _free_port() -> int:
    """Return a TCP port the OS is currently willing to hand out."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(base_url: str, timeout: float = 30.0) -> None:
    """Block until `/api/health` returns 200, or raise after `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{base_url}/api/health", timeout=1.0)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"backend at {base_url} did not become healthy in {timeout}s")


@pytest.fixture
def backend(test_db: str) -> Generator[str]:
    """Spawn a backend serving the built frontend; yield its URL; tear down.

    Depends on `test_db` so each test gets a fresh nonce database.
    The backend subprocess inherits the test's environment but with
    DATABASE_URL pointed at the nonce DB and PORT set to a free port.
    """
    if not _FRONTEND_DIST.is_dir():
        msg = (
            f"frontend/dist not found at {_FRONTEND_DIST}; run "
            "`just build` (or `cd frontend && npm run build`) first"
        )
        raise RuntimeError(msg)

    port = _free_port()
    backend_url = f"http://127.0.0.1:{port}"

    env = {
        **os.environ,
        "DATABASE_URL": test_db,
        "PORT": str(port),
        "HOST": "127.0.0.1",
    }

    proc = subprocess.Popen(
        ["uv", "run", "alpha"],  # noqa: S607
        cwd=_BACKEND_ROOT,
        env=env,
    )
    try:
        _wait_for_health(backend_url)
        yield backend_url
    finally:
        proc.terminate()
        try:
            _ = proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            _ = proc.wait()


@pytest.fixture(scope="session")
def browser_context_args() -> dict[str, object]:
    """Tell Playwright to ignore self-signed certs and route to our local server."""
    return {"ignore_https_errors": True}

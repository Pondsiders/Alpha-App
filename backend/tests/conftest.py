"""Test fixtures for the Alpha-App integration suite.

`test_db` (session-scoped) provisions a nonce `test_<nanoid>` database
on `sandbox-db` via the `test_runner` role, runs Alembic migrations
against it, yields its URL, and drops it after the session.

`client` (function-scoped) monkey-patches `settings.database_url` to
point at the nonce database, constructs the app via `create_app()`,
and yields a `TestClient`. Entering the `with` block runs the FastAPI
lifespan (which opens the asyncpg pool); leaving it closes the pool.
"""

from collections.abc import Generator
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import nanoid
import psycopg
import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql

from alembic import command
from alpha.app import create_app
from alpha.settings import settings

_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _swap_database(url: str, dbname: str) -> str:
    """Return `url` with its database path replaced by `dbname`."""
    parts = urlparse(url)
    return urlunparse(parts._replace(path=f"/{dbname}"))


@pytest.fixture(scope="session")
def test_db() -> Generator[str]:
    """Provision a nonce test database, run migrations, yield URL, drop after."""
    if settings.test_runner_url is None:
        pytest.skip("test_runner_url not configured in settings.toml")

    admin_url = settings.test_runner_url
    dbname = f"test_{nanoid.generate(size=12).lower().replace('-', '_')}"
    test_url = _swap_database(admin_url, dbname)

    # CREATE DATABASE has to run outside a transaction; AUTOCOMMIT does that.
    with psycopg.connect(admin_url, autocommit=True) as admin, admin.cursor() as cur:
        _ = cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))

    # Alembic's env.py reads `settings.database_url` and injects it into
    # SQLAlchemy. To run migrations against the test database, swap that
    # field on the singleton before invoking `command.upgrade`, then put
    # it back. (set_main_option won't work because env.py overrides it.)
    original_url = settings.database_url
    try:
        settings.database_url = test_url
        cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
        command.upgrade(cfg, "head")

        yield test_url
    finally:
        settings.database_url = original_url
        with (
            psycopg.connect(admin_url, autocommit=True) as admin,
            admin.cursor() as cur,
        ):
            _ = cur.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(dbname))
            )


@pytest.fixture
def client(test_db: str, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    """Yield a TestClient bound to a fresh app pointed at the test database."""
    monkeypatch.setattr(settings, "database_url", test_db)
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

"""Test fixtures for the Alpha-App integration suite.

`test_db` (function-scoped) provisions a nonce `test_<nanoid>` database
on the cluster pointed at by `settings.test_database_url`, runs Alembic
migrations against it, yields its URL, and drops it after the test.
Fresh database per test means rows from one test never leak into another.

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
from alpha.settings import Mode, settings

if settings.mode != Mode.TEST:
    msg = (
        f"tests refuse to run unless MODE=test; got MODE={settings.mode.value}. "
        "Run via `just test` / `just e2e`, or set MODE=test in the environment."
    )
    raise RuntimeError(msg)

_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _swap_database(url: str, dbname: str) -> str:
    """Return `url` with its database path replaced by `dbname`."""
    parts = urlparse(url)
    return urlunparse(parts._replace(path=f"/{dbname}"))


@pytest.fixture
def test_db() -> Generator[str]:
    """Provision a nonce test database, run migrations, yield URL, drop after."""
    if settings.test_database_url is None:
        pytest.skip("test_database_url not configured")

    admin_url = settings.test_database_url
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

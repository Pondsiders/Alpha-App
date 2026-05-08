"""Wipe Alpha-App's owned schemas in the dev database, re-apply migrations.

Reads `database_url` from `backend/settings.toml` via the same Settings
machinery the app uses, drops every schema this app creates (today: `app`),
drops `public.alembic_version`, then runs `alembic upgrade head`.

Dev-only. Production deploys never run this — production databases are
whatever the hosting provider gave us, however they were provisioned.

Run from `backend/`:

    uv run python scripts/dev/reset_dev_db.py
"""

import subprocess
import sys
from urllib.parse import urlparse

import psycopg

from alpha.settings import settings


def _confirm_dev(database_url: str) -> None:
    """Refuse to run against anything that isn't the sandbox dev database."""
    parsed = urlparse(database_url)
    host = parsed.hostname or ""
    dbname = parsed.path.lstrip("/")

    if host != "sandbox-db.tail8bd569.ts.net":
        msg = (
            f"refusing to reset: database host is {host!r}, "
            f"expected 'sandbox-db.tail8bd569.ts.net'. "
            f"This script is dev-only; pointing it at anything else is a bug."
        )
        raise RuntimeError(msg)

    if not dbname.startswith("pre_v1_0_0"):
        msg = (
            f"refusing to reset: database name is {dbname!r}, "
            f"expected to start with 'pre_v1_0_0'. "
            f"Production databases don't get reset."
        )
        raise RuntimeError(msg)


def main() -> None:
    """Drop owned schemas, re-run migrations."""
    _confirm_dev(settings.database_url)

    parsed = urlparse(settings.database_url)
    print(f"→ dropping schemas owned by alpha in {parsed.path.lstrip('/')}")
    with psycopg.connect(settings.database_url) as conn, conn.cursor() as cur:
        _ = cur.execute("DROP SCHEMA IF EXISTS app CASCADE")
        _ = cur.execute("DROP TABLE IF EXISTS public.alembic_version")
        conn.commit()

    print("→ alembic upgrade head")
    _ = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],  # noqa: S607 — uv on PATH is the convention
        check=True,
    )

    print("✓ dev database reset")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, psycopg.Error, subprocess.CalledProcessError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

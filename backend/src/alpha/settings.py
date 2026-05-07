"""Settings — single source of truth for every env-driven knob.

Pydantic Settings validates types and refuses extras. Add new fields here;
never read `os.environ` directly elsewhere.

Import the `settings` instance, not the class:

    from alpha.settings import settings

Config sources, in order of precedence (later wins):

1. **`.env` next to `pyproject.toml`** — dev convenience. `env_file` is
   anchored to a path computed from `__file__`, so `uv run alpha` works
   regardless of cwd. Not deployed; the repo's `.env` stays in `backend/`.
2. **Real environment variables** — production path. systemd's
   `EnvironmentFile=` and Docker Compose's `env_file:` both populate
   `os.environ` before the program runs; Pydantic reads from there. The
   program is the same in dev and prod; only the injection mechanism
   differs.
"""

from pathlib import Path
from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict

# `settings.py` lives at `backend/src/alpha/settings.py`. Three `.parent`s
# up from this file is `backend/`, where `pyproject.toml` and `.env` sit.
# Anchored to file location, not cwd — invocation directory doesn't matter.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Alpha-App configuration."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=_BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    host: str = "127.0.0.1"
    port: int = 8000
    dev: bool = False

    # Read by the logfire library directly from os.environ; declared here
    # so `extra="forbid"` doesn't reject it when present in .env.
    logfire_token: str | None = None


settings = Settings()

"""Configuration via environment variables.

`backend/.env` is read as a fallback for fields not present in the
process environment.

Import the `settings` instance:

    from alpha.settings import settings
"""

import os
from pathlib import Path
from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Alpha-App configuration."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=_BACKEND_ROOT / ".env",
        extra="forbid",
        case_sensitive=False,
    )

    database_url: str
    logfire_token: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    test_runner_url: str | None = None


settings = Settings()  # pyright: ignore[reportCallIssue]


# Remove from os.environ the variables Settings has captured that should
# not be visible to child processes spawned by this program.
_ = os.environ.pop("DATABASE_URL", None)

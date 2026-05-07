"""Settings — single source of truth for every env-driven knob.

Pydantic Settings validates types and refuses extras. Add new fields here;
never read `os.environ` directly elsewhere.

Import the `settings` instance, not the class:

    from alpha.settings import settings
"""

from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Alpha-App configuration."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env",
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

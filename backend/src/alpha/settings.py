"""Configuration via environment variables.

`backend/.env` is read as a fallback for fields not present in the
process environment.

Import the `settings` instance:

    from alpha.settings import settings
"""

import os
from pathlib import Path
from typing import ClassVar

from pydantic import computed_field, model_validator
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

    je_ne_sais_quoi: Path
    """Claude Code plugin directory. Must contain `.claude-plugin/plugin.json`."""

    working_directory: Path
    """Working directory for the Claude subprocess. Must be an absolute path
    to an existing directory."""

    @computed_field
    @property
    def soul_doc(self) -> Path:
        """Path to the soul prompt within the plugin directory."""
        return self.je_ne_sais_quoi / "prompts" / "soul.md"

    @model_validator(mode="after")
    def _validate_paths(self) -> "Settings":
        """Refuse to start with a misconfigured plugin or working directory."""
        manifest = self.je_ne_sais_quoi / ".claude-plugin" / "plugin.json"
        if not manifest.is_file():
            msg = (
                f"je_ne_sais_quoi must be a Claude Code plugin directory; "
                f"{manifest} not found"
            )
            raise ValueError(msg)
        if not self.soul_doc.is_file():
            msg = f"alpha soul not found at {self.soul_doc}"
            raise ValueError(msg)
        if not self.working_directory.is_absolute():
            msg = (
                f"working_directory must be an absolute path; "
                f"got {self.working_directory}"
            )
            raise ValueError(msg)
        if not self.working_directory.is_dir():
            msg = (
                f"working_directory must be an existing directory; "
                f"got {self.working_directory}"
            )
            raise ValueError(msg)
        return self


settings = Settings()  # pyright: ignore[reportCallIssue]


# Variables Settings has captured that should not be visible to child
# processes spawned by this program.
_ = os.environ.pop("DATABASE_URL", None)

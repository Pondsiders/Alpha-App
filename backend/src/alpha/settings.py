"""Configuration via environment variables.

`backend/.env` is read as a fallback for fields not present in the
process environment.

Import the `settings` instance:

    from alpha.settings import settings
"""

import os
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


class Mode(StrEnum):
    """Deployment mode. Selects the rules the rest of the app plays by."""

    PROD = "prod"
    DEV = "dev"
    TEST = "test"


class Settings(BaseSettings):
    """Alpha-App configuration."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=_BACKEND_ROOT / ".env",
        extra="forbid",
        case_sensitive=False,
    )

    mode: Mode = Mode.PROD
    database_url: str
    test_database_url: str | None = None
    """Admin URL of a cluster (`postgres` database) the test runner uses to
    create per-test nonce databases. Only the test fixture reads this; the
    app's runtime code never does."""

    logfire_token: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000

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

    @model_validator(mode="after")
    def _validate_mode_vs_test_database(self) -> "Settings":
        """Production refuses to know about test infrastructure."""
        if self.mode == Mode.PROD and self.test_database_url is not None:
            msg = (
                "MODE=prod refuses to start when TEST_DATABASE_URL is set. "
                "Production does not get to know there is a test cluster."
            )
            raise ValueError(msg)
        return self


settings = Settings()  # pyright: ignore[reportCallIssue]


# Variables Settings has captured that should not be visible to child
# processes spawned by this program.
_ = os.environ.pop("DATABASE_URL", None)
_ = os.environ.pop("TEST_DATABASE_URL", None)

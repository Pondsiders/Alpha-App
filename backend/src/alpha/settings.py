"""Settings — single source of truth for every config knob.

All Alpha-App config is read from one TOML file.

**Resolution.** Settings looks for the config file at, in order:

1. `<backend_root>/settings.toml` — dev.
2. `/etc/alpha/settings.toml` — production deploy.

Whichever exists first wins. If neither exists, import fails loudly.

Import the `settings` instance, not the class:

    from alpha.settings import settings
"""

from pathlib import Path
from typing import ClassVar, override

from pydantic_settings import BaseSettings, SettingsConfigDict, TomlConfigSettingsSource
from pydantic_settings.sources import PydanticBaseSettingsSource

# `settings.py` lives at `backend/src/alpha/settings.py`. Three `.parent`s
# up from this file is `backend/`.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

_DEV_CONFIG = _BACKEND_ROOT / "settings.toml"
_PROD_CONFIG = Path("/etc/alpha/settings.toml")


def _resolve_config_path() -> Path:
    """Pick the first config file that exists. Fail loudly if neither does."""
    for candidate in (_DEV_CONFIG, _PROD_CONFIG):
        if candidate.exists():
            return candidate
    msg = (
        f"No Alpha-App config file found. Expected one of:\n"
        f"  {_DEV_CONFIG}\n"
        f"  {_PROD_CONFIG}\n"
        f"Create one with at least database_url set."
    )
    raise RuntimeError(msg)


class Settings(BaseSettings):
    """Alpha-App configuration."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        toml_file=_resolve_config_path(),
        extra="forbid",
    )

    # Required. The program is useless without a database; a missing
    # value should explode loudly at import time.
    database_url: str

    # Optional. When set, Logfire is configured with this token; when
    # unset, Logfire is configured with telemetry disabled.
    logfire_token: str | None = None

    # HTTP listen address. Override in the TOML file if Tailscale Serve
    # or some other fronting proxy needs the listener bound elsewhere.
    host: str = "127.0.0.1"
    port: int = 8000

    # Optional. Connection string for a role with CREATEDB privilege,
    # used by the integration test harness to spin up nonce databases
    # named `test_<nanoid>`. Unset in production.
    test_runner_url: str | None = None

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Use only the TOML source, plus init for test overrides."""
        return (init_settings, TomlConfigSettingsSource(settings_cls))


# Pydantic populates required fields from the TOML source at
# instantiation; basedpyright doesn't model that.
settings = Settings()  # pyright: ignore[reportCallIssue]

"""Console entry point — `uv run alpha`."""

import uvicorn

from alpha.settings import settings


def main() -> None:
    """Stand up the Alpha-App backend."""
    uvicorn.run(
        "alpha.app:create_app",
        host=settings.host,
        port=settings.port,
        factory=True,
    )

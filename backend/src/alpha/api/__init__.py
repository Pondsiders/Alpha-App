"""HTTP API surface — mounted at /api by `app.create_app()`."""

from alpha.api.router import router

__all__ = ["router"]

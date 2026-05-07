"""HTTP API surface — mounted at /api by `app.create_app()`.

This package's `router` is what `app.py` mounts. Today the API has one
feature (`health`); when more arrive, add them to the composition below
and import their sub-routers here.
"""

from fastapi import APIRouter

from alpha.api import health

router = APIRouter()
router.include_router(health.router)

__all__ = ["router"]

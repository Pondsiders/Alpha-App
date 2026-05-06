"""Top-level /api router — mounts feature sub-routers."""

from fastapi import APIRouter

from alpha.api import health

router = APIRouter()
router.include_router(health.router)

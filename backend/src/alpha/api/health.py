"""GET /api/health — liveness check."""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Return ok if the process is alive enough to answer."""
    return {"status": "ok"}

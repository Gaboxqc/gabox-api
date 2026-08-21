from fastapi import APIRouter

from api.statpitch.accounts.router import router as accounts_router
from api.statpitch.admin import admin_router

from .fixtures import router as fixtures_router

statpitch_router = APIRouter()

statpitch_router.include_router(fixtures_router, tags=["StatPitch"])
# Mounted here rather than at the app root so every customer-facing path lives
# under /statpitch, well away from the admin's /auth.
statpitch_router.include_router(accounts_router)
# Admin-guarded, and mounted under the same /statpitch prefix so the whole
# module lives at one path — the guard is the boundary, not the URL.
statpitch_router.include_router(admin_router)

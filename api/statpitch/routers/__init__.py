from fastapi import APIRouter

from api.statpitch.accounts.router import router as accounts_router

from .fixtures import router as fixtures_router

statpitch_router = APIRouter()

statpitch_router.include_router(fixtures_router, tags=["StatPitch"])
# Mounted here rather than at the app root so every customer-facing path lives
# under /statpitch, well away from the admin's /auth.
statpitch_router.include_router(accounts_router)

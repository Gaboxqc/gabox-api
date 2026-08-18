from fastapi import APIRouter

from .fixtures import router as fixtures_router

statpitch_router = APIRouter()

statpitch_router.include_router(fixtures_router, tags=["StatPitch"])

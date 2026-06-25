from fastapi import APIRouter

from .predictions import router as predictions_router

statpitch_router = APIRouter()

statpitch_router.include_router(predictions_router, tags=["StatPitch: Predictions"])

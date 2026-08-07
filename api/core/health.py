"""Liveness and readiness endpoints."""

import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from api.core.database import SessionDep

log = logging.getLogger("api.health")

router = APIRouter(tags=["Health"])


@router.get("/", summary="Service index")
async def index():
    return {
        "status": "online",
        "projects": {
            "portfolio": "/portfolio",
            "statpitch": "/statpitch",
        },
        "docs": "/docs",
        "health": "/health",
    }


@router.get(
    "/health",
    summary="Readiness check",
    description=(
        "Returns 200 only when the database is reachable, so an uptime monitor "
        "sees a failure when the app is up but cannot serve data."
    ),
)
async def health(db: SessionDep, response: Response):
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:
        log.error("Health check failed to reach the database: %s", exc)
        database = "unreachable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if database == "ok" else "degraded",
        "database": database,
    }

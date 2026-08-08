from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.core.auth import auth_router
from api.core.config import settings
from api.core.errors import register_exception_handlers
from api.core.health import router as health_router
from api.core.logging import configure_logging
from api.portfolio.routers import portfolio_router
from api.statpitch.routers import statpitch_router


def create_app() -> FastAPI:
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Gabox API",
        description=(
            "Centralized serverless backend for all my projects. "
            "Navigate to /docs for interactive documentation."
        ),
        version="2.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(portfolio_router, prefix="/portfolio")
    app.include_router(statpitch_router, prefix="/statpitch")

    return app


app = create_app()

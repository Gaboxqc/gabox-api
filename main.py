from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.core.audit import AuditMiddleware
from api.core.auth import auth_router
from api.core.config import settings
from api.core.errors import register_exception_handlers
from api.core.headers import SecurityHeadersMiddleware
from api.core.health import router as health_router
from api.core.logging import configure_logging
from api.portfolio.routers import portfolio_router
from api.statpitch.routers import statpitch_router
from api.statpitch.routers.fixtures import QUOTA_HEADER


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

    # Starlette runs middleware in reverse registration order, so these are
    # added before CORS to leave CORS outermost — its preflight short-circuit
    # must still carry the access-control headers.
    app.add_middleware(AuditMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.security_hsts)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # A cross-origin response's custom headers are invisible to JavaScript
        # unless they are listed here, so the dashboard's pagination would read
        # undefined without this — as would StatPitch's remaining-predictions
        # count, which is the whole signal behind the free tier's upsell.
        expose_headers=["X-Total-Count", QUOTA_HEADER],
    )

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(portfolio_router, prefix="/portfolio")
    app.include_router(statpitch_router, prefix="/statpitch")

    return app


app = create_app()

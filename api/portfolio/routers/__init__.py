from fastapi import APIRouter

from .tags import router as tag_router
from .projects import router as project_router
from .project_translation import router as project_translation_router
from .academy import router as academy_router
from .certificate import router as certificate_router

portfolio_router = APIRouter()

portfolio_router.include_router(tag_router, tags=["Portfolio: Tags"])
portfolio_router.include_router(project_router, tags=["Portfolio: Project"])
portfolio_router.include_router(project_translation_router, tags=["Portfolio: Project Translation"])
portfolio_router.include_router(academy_router, tags=["Portfolio: Academy"])
portfolio_router.include_router(certificate_router, tags=["Portfolio: Certificate"])
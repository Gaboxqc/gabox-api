from fastapi import APIRouter

from .tags import router as tag_router
from .projects import router as project_router

portfolio_router = APIRouter()

portfolio_router.include_router(tag_router, prefix="/tags", tags=["Portfolio: Tags"])
portfolio_router.include_router(project_router, prefix="/tags", tags=["Portfolio: Project"])


from fastapi import APIRouter

from .certification import router as certification_router
from .certification_translation import router as certification_translation_router
from .course import router as course_router
from .course_translation import router as course_translation_router
from .lookups import lookup_routers
from .project_translation import router as project_translation_router
from .projects import router as project_router

portfolio_router = APIRouter()

# Simple id + name tables, all sharing one contract (see .lookups).
for _lookup_router in lookup_routers:
    portfolio_router.include_router(_lookup_router)

# Resources with their own filtering and eager-loading rules.
portfolio_router.include_router(project_router, tags=["Portfolio: Projects"])
portfolio_router.include_router(course_router, tags=["Portfolio: Courses"])
portfolio_router.include_router(certification_router, tags=["Portfolio: Certifications"])
portfolio_router.include_router(
    project_translation_router, tags=["Portfolio: Project Translations"]
)
portfolio_router.include_router(course_translation_router, tags=["Portfolio: Course Translations"])
portfolio_router.include_router(
    certification_translation_router, tags=["Portfolio: Certification Translations"]
)

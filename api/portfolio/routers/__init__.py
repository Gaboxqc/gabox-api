from fastapi import APIRouter

from .certification import router as certification_router
from .course import router as course_router
from .lookups import lookup_routers
from .projects import router as project_router
from .translations import translation_routers

portfolio_router = APIRouter()

# Simple id + name tables, all sharing one contract (see .lookups).
for _router in lookup_routers:
    portfolio_router.include_router(_router)

# Resources with their own filtering and eager-loading rules.
portfolio_router.include_router(project_router, tags=["Portfolio: Projects"])
portfolio_router.include_router(course_router, tags=["Portfolio: Courses"])
portfolio_router.include_router(certification_router, tags=["Portfolio: Certifications"])

# Per-language content hanging off each of the three above (see .translations).
for _router in translation_routers:
    portfolio_router.include_router(_router)

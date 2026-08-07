"""Lookup resources — the simple `id + name` tables behind projects,
certifications and courses.

Each one gets the identical five-route contract from `crud_router`. Declaring
them side by side makes any future divergence obvious, which is how the six
hand-written versions drifted apart in the first place.
"""

from api.core.crud import crud_router
from api.portfolio.models import (
    Academy,
    AcademyCreate,
    AcademyRead,
    AcademyUpdate,
    Category,
    CategoryCreate,
    CategoryRead,
    CategoryUpdate,
    DifficultyLevel,
    DifficultyLevelCreate,
    DifficultyLevelRead,
    DifficultyLevelUpdate,
    Language,
    LanguageCreate,
    LanguageRead,
    LanguageUpdate,
    ProjectType,
    ProjectTypeCreate,
    ProjectTypeRead,
    ProjectTypeUpdate,
    Tag,
    TagCreate,
    TagRead,
    TagUpdate,
)

tag_router = crud_router(
    model=Tag,
    create_schema=TagCreate,
    read_schema=TagRead,
    update_schema=TagUpdate,
    prefix="/tags",
    tag="Portfolio: Tags",
    resource="tag",
)

academy_router = crud_router(
    model=Academy,
    create_schema=AcademyCreate,
    read_schema=AcademyRead,
    update_schema=AcademyUpdate,
    prefix="/academies",
    tag="Portfolio: Academies",
    resource="academy",
)

category_router = crud_router(
    model=Category,
    create_schema=CategoryCreate,
    read_schema=CategoryRead,
    update_schema=CategoryUpdate,
    prefix="/categories",
    tag="Portfolio: Categories",
    resource="category",
)

project_type_router = crud_router(
    model=ProjectType,
    create_schema=ProjectTypeCreate,
    read_schema=ProjectTypeRead,
    update_schema=ProjectTypeUpdate,
    prefix="/project-types",
    tag="Portfolio: Project Types",
    resource="project_type",
)

difficulty_level_router = crud_router(
    model=DifficultyLevel,
    create_schema=DifficultyLevelCreate,
    read_schema=DifficultyLevelRead,
    update_schema=DifficultyLevelUpdate,
    prefix="/difficulty-levels",
    tag="Portfolio: Difficulty Levels",
    resource="difficulty_level",
)

# `code` ("en", "es") is the primary key here, not an integer id.
language_router = crud_router(
    model=Language,
    create_schema=LanguageCreate,
    read_schema=LanguageRead,
    update_schema=LanguageUpdate,
    prefix="/languages",
    tag="Portfolio: Languages",
    resource="language",
    pk_type=str,
)

lookup_routers = [
    tag_router,
    academy_router,
    category_router,
    project_type_router,
    difficulty_level_router,
    language_router,
]

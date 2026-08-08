from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlmodel import select

from api.core.auth.deps import require_admin
from api.core.database import SessionDep
from api.core.deps import PageDep, eager_options, escape_like, get_or_404, get_with_relations
from api.portfolio.deps import apply_tag_ids, count_matching, resolve_tags, set_total_count
from api.portfolio.models import (
    Project,
    ProjectCreate,
    ProjectReadComplete,
    ProjectTranslation,
    ProjectUpdate,
    Tag,
)

router = APIRouter(prefix="/projects")
authenticated = [Depends(require_admin)]

# Everything ProjectReadComplete nests, eager-loaded to avoid N+1 queries.
RELATIONS = (
    Project.project_type,
    Project.difficulty_level,
    Project.tags,
    Project.translations,
)


def _load(db: SessionDep, project_id: int) -> Project:
    return get_with_relations(db, Project, project_id, RELATIONS)


@router.post(
    "",
    response_model=ProjectReadComplete,
    status_code=status.HTTP_201_CREATED,
    dependencies=authenticated,
)
async def create_project(project_data: ProjectCreate, db: SessionDep):
    payload = project_data.model_dump()
    # tag_ids is not a column, so it is resolved into related rows separately.
    tag_ids = payload.pop("tag_ids", [])

    project = Project.model_validate(payload)
    project.tags = resolve_tags(db, tag_ids)
    db.add(project)
    db.commit()
    db.refresh(project)
    return _load(db, project.id)


@router.get("", response_model=list[ProjectReadComplete])
async def list_projects(
    response: Response,
    db: SessionDep,
    page: PageDep,
    is_main: Annotated[bool | None, Query(description="Filter featured projects only")] = None,
    search: Annotated[str | None, Query(description="Search by project title")] = None,
    project_type_id: Annotated[
        list[int] | None, Query(description="Filter by project type ID")
    ] = None,
    difficulty_level_id: Annotated[
        list[int] | None, Query(description="Filter by difficulty level ID")
    ] = None,
    tag_id: Annotated[list[int] | None, Query(description="Filter by tag ID")] = None,
):
    query = select(Project)

    if is_main is not None:
        query = query.where(Project.is_main == is_main)

    if search:
        query = query.where(
            Project.translations.any(
                ProjectTranslation.title.ilike(f"%{escape_like(search)}%", escape="\\")
            )
        )

    if project_type_id:
        query = query.where(Project.project_type_id.in_(project_type_id))

    if difficulty_level_id:
        query = query.where(Project.difficulty_level_id.in_(difficulty_level_id))

    # `.any()` compiles to EXISTS, so rows are never duplicated and no DISTINCT
    # is needed — which matters because DISTINCT fights with LIMIT/OFFSET.
    if tag_id:
        query = query.where(Project.tags.any(Tag.id.in_(tag_id)))

    set_total_count(response, count_matching(db, query))

    query = query.options(*eager_options(RELATIONS)).order_by(Project.id)
    return db.exec(query.offset(page.offset).limit(page.limit)).all()


@router.get("/{project_id}", response_model=ProjectReadComplete)
async def get_project(project_id: int, db: SessionDep):
    return _load(db, project_id)


@router.patch(
    "/{project_id}",
    response_model=ProjectReadComplete,
    dependencies=authenticated,
)
async def update_project(project_id: int, project_data: ProjectUpdate, db: SessionDep):
    project = get_or_404(db, Project, project_id)
    changes = project_data.model_dump(exclude_unset=True)
    tag_ids = changes.pop("tag_ids", None)

    for field, value in changes.items():
        setattr(project, field, value)
    apply_tag_ids(db, project, tag_ids)

    db.add(project)
    db.commit()
    return _load(db, project_id)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=authenticated,
)
async def delete_project(project_id: int, db: SessionDep):
    db.delete(get_or_404(db, Project, project_id))
    db.commit()

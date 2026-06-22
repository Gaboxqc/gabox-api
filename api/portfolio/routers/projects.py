from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import selectinload
from sqlmodel import select

from api.database import SessionDep
from api.portfolio.models import (
    Project,
    ProjectCreate,
    ProjectReadComplete,
    ProjectTranslation,
    ProjectUpdate,
    Tag,
)
from api.security import validate_api_key

router = APIRouter()


def _load_project(project_id: int, db: SessionDep) -> ProjectReadComplete:
    project = db.exec(
        select(Project)
        .where(Project.id == project_id)
        .options(
            selectinload(Project.project_type),
            selectinload(Project.difficulty_level),
            selectinload(Project.tags),
            selectinload(Project.translations),
        )
    ).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project with id {project_id} not found",
        )
    return project


@router.post(
    "/projects",
    response_model=ProjectReadComplete,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(validate_api_key)],
)
async def create_project(project_data: ProjectCreate, db: SessionDep):
    new_project = Project.model_validate(project_data.model_dump())
    db.add(new_project)
    db.commit()
    db.refresh(new_project)
    return _load_project(new_project.id, db)


@router.get("/projects/{project_id}", response_model=ProjectReadComplete)
async def get_project(project_id: int, db: SessionDep):
    return _load_project(project_id, db)


@router.get("/projects", response_model=list[ProjectReadComplete])
async def get_projects(
    db: SessionDep,
    is_main: Annotated[bool | None, Query(description="Filter featured projects only")] = None,
    search: Annotated[str | None, Query(description="Search by project title")] = None,
    project_type_id: Annotated[
        list[int] | None, Query(description="Filter by project type ID")
    ] = None,
    difficulty_level_id: Annotated[
        list[int] | None, Query(description="Filter by difficulty level ID")
    ] = None,
    tag_id: Annotated[list[int] | None, Query(description="Filter by tag ID")] = None,
    offset: int = 0,
    limit: Annotated[int, Query(le=100)] = 10,
):
    if project_type_id is None:
        project_type_id = []
    if difficulty_level_id is None:
        difficulty_level_id = []
    if tag_id is None:
        tag_id = []

    query = select(Project)

    if is_main is not None:
        query = query.where(Project.is_main == is_main)

    if search:
        query = query.where(Project.translations.any(ProjectTranslation.title.ilike(f"%{search}%")))

    if project_type_id:
        query = query.where(Project.project_type_id.in_(project_type_id))

    if difficulty_level_id:
        query = query.where(Project.difficulty_level_id.in_(difficulty_level_id))

    if tag_id:
        query = query.where(Project.tags.any(Tag.id.in_(tag_id)))

    query = query.options(
        selectinload(Project.project_type),
        selectinload(Project.difficulty_level),
        selectinload(Project.tags),
        selectinload(Project.translations),
    )

    return db.exec(query.offset(offset).limit(limit)).all()


@router.patch(
    "/projects/{project_id}",
    response_model=ProjectReadComplete,
    dependencies=[Depends(validate_api_key)],
)
async def update_project(project_id: int, project_data: ProjectUpdate, db: SessionDep):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project with id {project_id} not found",
        )
    update_data = project_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(project, key, value)
    db.add(project)
    db.commit()
    return _load_project(project_id, db)


@router.delete(
    "/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(validate_api_key)],
)
async def delete_project(project_id: int, db: SessionDep):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project with id {project_id} not found",
        )
    db.delete(project)
    db.commit()
    return None

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import select

from api.database import SessionDep
from api.portfolio.models import ProjectType, ProjectTypeCreate, ProjectTypeRead
from api.security import validate_api_key

router = APIRouter()


@router.post(
    "/project-types",
    response_model=ProjectTypeRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(validate_api_key)],
)
async def create_project_type(
    type_data: ProjectTypeCreate,
    db: SessionDep,
):
    new_type = ProjectType.model_validate(type_data.model_dump())
    db.add(new_type)
    db.commit()
    db.refresh(new_type)
    return new_type


@router.get("/project-types", response_model=list[ProjectTypeRead])
async def list_project_types(
    db: SessionDep,
    offset: int = 0,
    limit: Annotated[int, Query(le=100)] = 10,
):
    return db.exec(select(ProjectType).offset(offset).limit(limit)).all()


@router.delete(
    "/project-types/{type_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(validate_api_key)],
)
async def delete_project_type(type_id: int, db: SessionDep):
    project_type = db.get(ProjectType, type_id)
    if not project_type:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project type with id {type_id} not found",
        )
    db.delete(project_type)
    db.commit()
    return None

from typing import List
from fastapi import APIRouter, status, HTTPException
from sqlmodel import select

from api.database import SessionDep
from api.portfolio.models import Project, ProjectCreate, ProjectUpdate

router = APIRouter()

@router.post("/project", response_model=Project, status_code=status.HTTP_201_CREATED)
async def create_project(project_data: ProjectCreate, db: SessionDep):
    new_project = Project.model_validate(project_data.model_dump())
    db.add(new_project)
    db.commit()
    db.refresh(new_project)
    return new_project

@router.get("/project", response_model=List[Project])
async def list_project(db: SessionDep):
    return db.exec(select(Project)).all()

@router.get("/project/{project_id}", response_model=Project)
async def get_project(project_id: int, db: SessionDep):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project with id {project_id} not found")
    return project

@router.patch("/project/{project_id}", response_model=Project)
async def update_project(project_id: int, project_data: ProjectUpdate, db: SessionDep):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project with id {project_id} not found")
    update_dic = project_data.model_dump(exclude_unset=True)
    for key, value in update_dic.items():
        setattr(project, key, value)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project

@router.delete("/project/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: int, db: SessionDep):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project with id {project_id} not found")
    db.delete(project)
    db.commit()
    return None
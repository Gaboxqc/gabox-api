from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select

from api.core.database import SessionDep
from api.portfolio.models import (
    Project,
    ProjectTranslation,
    ProjectTranslationCreate,
    ProjectTranslationUpdate,
)
from api.core.security import validate_api_key

router = APIRouter()


def _get_or_404(project_id: int, language_code: str, db: SessionDep) -> ProjectTranslation:
    translation = db.exec(
        select(ProjectTranslation).where(
            ProjectTranslation.project_id == project_id,
            ProjectTranslation.language_code == language_code,
        )
    ).first()
    if not translation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Translation for project {project_id} in '{language_code}' not found",
        )
    return translation


@router.post(
    "/projects/{project_id}/translations",
    response_model=ProjectTranslation,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(validate_api_key)],
)
async def create_project_translation(
    project_id: int,
    translation_data: ProjectTranslationCreate,
    db: SessionDep,
):
    # Verify the parent project exists
    if not db.get(Project, project_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project with id {project_id} not found",
        )

    existing = db.exec(
        select(ProjectTranslation).where(
            ProjectTranslation.project_id == project_id,
            ProjectTranslation.language_code == translation_data.language_code,
        )
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Translation for this project and language already exists.",
        )

    new_translation = ProjectTranslation.model_validate(
        {**translation_data.model_dump(), "project_id": project_id}
    )
    db.add(new_translation)
    db.commit()
    db.refresh(new_translation)
    return new_translation


@router.get(
    "/projects/{project_id}/translations",
    response_model=list[ProjectTranslation],
)
async def list_project_translations(project_id: int, db: SessionDep):
    if not db.get(Project, project_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project with id {project_id} not found",
        )
    return db.exec(
        select(ProjectTranslation).where(ProjectTranslation.project_id == project_id)
    ).all()


@router.get(
    "/projects/{project_id}/translations/{language_code}",
    response_model=ProjectTranslation,
)
async def get_project_translation(project_id: int, language_code: str, db: SessionDep):
    return _get_or_404(project_id, language_code, db)


@router.patch(
    "/projects/{project_id}/translations/{language_code}",
    response_model=ProjectTranslation,
    dependencies=[Depends(validate_api_key)],
)
async def update_project_translation(
    project_id: int,
    language_code: str,
    translation_data: ProjectTranslationUpdate,
    db: SessionDep,
):
    translation = _get_or_404(project_id, language_code, db)
    update_data = translation_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(translation, key, value)
    db.add(translation)
    db.commit()
    db.refresh(translation)
    return translation


@router.delete(
    "/projects/{project_id}/translations/{language_code}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(validate_api_key)],
)
async def delete_project_translation(project_id: int, language_code: str, db: SessionDep):
    translation = _get_or_404(project_id, language_code, db)
    db.delete(translation)
    db.commit()
    return None

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import select

from api.database import SessionDep
from api.portfolio.models import (
    Course,
    CourseTranslation,
    CourseTranslationCreate,
    CourseTranslationUpdate,
)
from api.security import validate_api_key

router = APIRouter()


def _get_or_404(course_id: int, language_code: str, db: SessionDep) -> Any | None:
    translation = db.exec(
        select(CourseTranslation).where(
            CourseTranslation.course_id == course_id,
            CourseTranslation.language_code == language_code,
        )
    ).first()
    if not translation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Translation for course {course_id} in '{language_code}' not found",
        )
    return translation


@router.post(
    "/courses/{course_id}/translations",
    response_model=CourseTranslation,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(validate_api_key)],
)
async def create_course_translation(
    course_id: int,
    translation_data: CourseTranslationCreate,
    db: SessionDep,
):
    if not db.get(Course, course_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Course with id {course_id} not found",
        )

    existing = db.exec(
        select(CourseTranslation).where(
            CourseTranslation.course_id == course_id,
            CourseTranslation.language_code == translation_data.language_code,
        )
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Translation for this course and language already exists.",
        )

    new_translation = CourseTranslation.model_validate(
        {**translation_data.model_dump(), "course_id": course_id}
    )
    db.add(new_translation)
    db.commit()
    db.refresh(new_translation)
    return new_translation


@router.get(
    "/courses/{course_id}/translations",
    response_model=list[CourseTranslation],
)
async def list_course_translations(
    course_id: int,
    db: SessionDep,
    offset: int = 0,
    limit: Annotated[int, Query(le=100)] = 10,
):
    if not db.get(Course, course_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Course with id {course_id} not found",
        )
    return db.exec(
        select(CourseTranslation)
        .where(CourseTranslation.course_id == course_id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.get(
    "/courses/{course_id}/translations/{language_code}",
    response_model=CourseTranslation,
)
async def get_course_translation(course_id: int, language_code: str, db: SessionDep):
    return _get_or_404(course_id, language_code, db)


@router.patch(
    "/courses/{course_id}/translations/{language_code}",
    response_model=CourseTranslation,
    dependencies=[Depends(validate_api_key)],
)
async def update_course_translation(
    course_id: int,
    language_code: str,
    translation_data: CourseTranslationUpdate,
    db: SessionDep,
):
    translation = _get_or_404(course_id, language_code, db)
    update_data = translation_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(translation, key, value)
    db.add(translation)
    db.commit()
    db.refresh(translation)
    return translation


@router.delete(
    "/courses/{course_id}/translations/{language_code}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(validate_api_key)],
)
async def delete_course_translation(course_id: int, language_code: str, db: SessionDep):
    translation = _get_or_404(course_id, language_code, db)
    db.delete(translation)
    db.commit()
    return None

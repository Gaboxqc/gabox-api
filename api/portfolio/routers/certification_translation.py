from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import select

from api.database import SessionDep
from api.portfolio.models import (
    Certification,
    CertificationTranslation,
    CertificationTranslationCreate,
    CertificationTranslationUpdate,
)
from api.security import validate_api_key

router = APIRouter()


def _get_or_404(certification_id: int, language_code: str, db: SessionDep) -> Any | None:
    translation = db.exec(
        select(CertificationTranslation).where(
            CertificationTranslation.certification_id == certification_id,
            CertificationTranslation.language_code == language_code,
        )
    ).first()
    if not translation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Translation for certification {certification_id} in '{language_code}' not found",  # noqa: E501
        )
    return translation


@router.post(
    "/certifications/{certification_id}/translations",
    response_model=CertificationTranslation,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(validate_api_key)],
)
async def create_certification_translation(
    certification_id: int,
    translation_data: CertificationTranslationCreate,
    db: SessionDep,
):
    if not db.get(Certification, certification_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Certification with id {certification_id} not found",
        )

    existing = db.exec(
        select(CertificationTranslation).where(
            CertificationTranslation.certification_id == certification_id,
            CertificationTranslation.language_code == translation_data.language_code,
        )
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Translation for this certification and language already exists.",
        )

    new_translation = CertificationTranslation.model_validate(
        {**translation_data.model_dump(), "certification_id": certification_id}
    )
    db.add(new_translation)
    db.commit()
    db.refresh(new_translation)
    return new_translation


@router.get(
    "/certifications/{certification_id}/translations",
    response_model=list[CertificationTranslation],
)
async def list_certification_translations(
    certification_id: int,
    db: SessionDep,
    offset: int = 0,
    limit: Annotated[int, Query(le=100)] = 10,
):
    if not db.get(Certification, certification_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Certification with id {certification_id} not found",
        )
    return db.exec(
        select(CertificationTranslation)
        .where(CertificationTranslation.certification_id == certification_id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.get(
    "/certifications/{certification_id}/translations/{language_code}",
    response_model=CertificationTranslation,
)
async def get_certification_translation(
    certification_id: int,
    language_code: str,
    db: SessionDep,
):
    return _get_or_404(certification_id, language_code, db)


@router.patch(
    "/certifications/{certification_id}/translations/{language_code}",
    response_model=CertificationTranslation,
    dependencies=[Depends(validate_api_key)],
)
async def update_certification_translation(
    certification_id: int,
    language_code: str,
    translation_data: CertificationTranslationUpdate,
    db: SessionDep,
):
    translation = _get_or_404(certification_id, language_code, db)
    update_data = translation_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(translation, key, value)
    db.add(translation)
    db.commit()
    db.refresh(translation)
    return translation


@router.delete(
    "/certifications/{certification_id}/translations/{language_code}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(validate_api_key)],
)
async def delete_certification_translation(
    certification_id: int,
    language_code: str,
    db: SessionDep,
):
    translation = _get_or_404(certification_id, language_code, db)
    db.delete(translation)
    db.commit()
    return None

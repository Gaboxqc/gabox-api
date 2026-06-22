from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from api.database import SessionDep
from api.portfolio.models import Language, LanguageCreate, LanguageRead
from api.security import validate_api_key

router = APIRouter()


@router.post(
    "/languages",
    response_model=LanguageRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(validate_api_key)],
)
async def create_language(
    language_data: LanguageCreate,
    db: SessionDep,
):
    existing = db.get(Language, language_data.code)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Language code already exists.",
        )
    new_language = Language.model_validate(language_data.model_dump())
    db.add(new_language)
    db.commit()
    db.refresh(new_language)
    return new_language


@router.get("/languages", response_model=list[LanguageRead])
async def list_languages(
    db: SessionDep,
    offset: int = 0,
    limit: Annotated[int, Query(le=100)] = 10,
):
    return db.exec(select(Language).offset(offset).limit(limit)).all()


@router.delete(
    "/languages/{code}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(validate_api_key)],
)
async def delete_language(code: str, db: SessionDep):
    language = db.get(Language, code)
    if not language:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Language '{code}' not found",
        )
    try:
        db.delete(language)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Cannot delete language: it is linked to active translations (RESTRICT constraint)."
            ),
        )

    return None

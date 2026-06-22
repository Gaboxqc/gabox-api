from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import selectinload
from sqlmodel import select

from api.database import SessionDep
from api.portfolio.models import (
    Certification,
    CertificationCreate,
    CertificationReadComplete,
    CertificationUpdate,
    Tag,
)
from api.security import validate_api_key

router = APIRouter()


def _load_certification(certification_id: int, db: SessionDep) -> CertificationReadComplete:
    certification = db.exec(
        select(Certification)
        .where(Certification.id == certification_id)
        .options(
            selectinload(Certification.academy),
            selectinload(Certification.category),
            selectinload(Certification.translations),
            selectinload(Certification.tags),
        )
    ).first()
    if not certification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Certification with id {certification_id} not found",
        )
    return certification


@router.post(
    "/certifications",
    response_model=CertificationReadComplete,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(validate_api_key)],
)
async def create_certification(certification_data: CertificationCreate, db: SessionDep):
    new_certification = Certification.model_validate(certification_data.model_dump())
    db.add(new_certification)
    db.commit()
    db.refresh(new_certification)
    return _load_certification(new_certification.id, db)


@router.get("/certifications/{certification_id}", response_model=CertificationReadComplete)
async def get_certification(certification_id: int, db: SessionDep):
    return _load_certification(certification_id, db)


@router.get("/certifications", response_model=list[CertificationReadComplete])
async def get_certifications(
    db: SessionDep,
    year: Annotated[int | None, Query(description="Filter by year of issue")] = None,
    academy_id: Annotated[int | None, Query(description="Filter by academy ID")] = None,
    category_id: Annotated[int | None, Query(description="Filter by category ID")] = None,
    tag_id: Annotated[list[int] | None, Query(description="Filter by tag ID")] = None,
    offset: int = 0,
    limit: Annotated[int, Query(le=100)] = 10,
):
    if tag_id is None:
        tag_id = []
    query = select(Certification)

    if year:
        query = query.where(Certification.year == year)

    if academy_id:
        query = query.where(Certification.academy_id == academy_id)

    if category_id:
        query = query.where(Certification.category_id == category_id)

    if tag_id:
        query = query.where(Certification.tags.any(Tag.id.in_(tag_id)))

    query = query.options(
        selectinload(Certification.academy),
        selectinload(Certification.category),
        selectinload(Certification.translations),
        selectinload(Certification.tags),
    )

    return db.exec(query.offset(offset).limit(limit)).all()


@router.patch(
    "/certifications/{certification_id}",
    response_model=CertificationReadComplete,
    dependencies=[Depends(validate_api_key)],
)
async def update_certification(
    certification_id: int,
    certification_data: CertificationUpdate,
    db: SessionDep,
):
    certification = db.get(Certification, certification_id)
    if not certification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Certification with id {certification_id} not found",
        )
    update_data = certification_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(certification, key, value)
    db.add(certification)
    db.commit()
    return _load_certification(certification_id, db)


@router.delete(
    "/certifications/{certification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(validate_api_key)],
)
async def delete_certification(certification_id: int, db: SessionDep):
    certification = db.get(Certification, certification_id)
    if not certification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Certification with id {certification_id} not found",
        )
    db.delete(certification)
    db.commit()
    return None

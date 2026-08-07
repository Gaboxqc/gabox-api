from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlmodel import select

from api.core.database import SessionDep
from api.core.deps import PageDep, eager_options, get_or_404, get_with_relations
from api.core.security import validate_api_key
from api.portfolio.models import (
    Certification,
    CertificationCreate,
    CertificationReadComplete,
    CertificationUpdate,
    Tag,
)

router = APIRouter(prefix="/certifications")
authenticated = [Depends(validate_api_key)]

RELATIONS = (
    Certification.academy,
    Certification.category,
    Certification.tags,
    Certification.translations,
)


def _load(db: SessionDep, certification_id: int) -> Certification:
    return get_with_relations(db, Certification, certification_id, RELATIONS)


@router.post(
    "",
    response_model=CertificationReadComplete,
    status_code=status.HTTP_201_CREATED,
    dependencies=authenticated,
)
async def create_certification(certification_data: CertificationCreate, db: SessionDep):
    certification = Certification.model_validate(certification_data.model_dump())
    db.add(certification)
    db.commit()
    db.refresh(certification)
    return _load(db, certification.id)


@router.get("", response_model=list[CertificationReadComplete])
async def list_certifications(
    db: SessionDep,
    page: PageDep,
    year: Annotated[int | None, Query(description="Filter by year of issue")] = None,
    academy_id: Annotated[int | None, Query(description="Filter by academy ID")] = None,
    category_id: Annotated[int | None, Query(description="Filter by category ID")] = None,
    tag_id: Annotated[list[int] | None, Query(description="Filter by tag ID")] = None,
):
    query = select(Certification)

    if year:
        query = query.where(Certification.year == year)

    if academy_id:
        query = query.where(Certification.academy_id == academy_id)

    if category_id:
        query = query.where(Certification.category_id == category_id)

    if tag_id:
        query = query.where(Certification.tags.any(Tag.id.in_(tag_id)))

    query = query.options(*eager_options(RELATIONS)).order_by(Certification.id)
    return db.exec(query.offset(page.offset).limit(page.limit)).all()


@router.get("/{certification_id}", response_model=CertificationReadComplete)
async def get_certification(certification_id: int, db: SessionDep):
    return _load(db, certification_id)


@router.patch(
    "/{certification_id}",
    response_model=CertificationReadComplete,
    dependencies=authenticated,
)
async def update_certification(
    certification_id: int,
    certification_data: CertificationUpdate,
    db: SessionDep,
):
    certification = get_or_404(db, Certification, certification_id)
    for field, value in certification_data.model_dump(exclude_unset=True).items():
        setattr(certification, field, value)
    db.add(certification)
    db.commit()
    return _load(db, certification_id)


@router.delete(
    "/{certification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=authenticated,
)
async def delete_certification(certification_id: int, db: SessionDep):
    db.delete(get_or_404(db, Certification, certification_id))
    db.commit()

from typing import List, Optional
from fastapi import APIRouter, status, HTTPException, Query, Depends
from sqlalchemy.orm import selectinload
from sqlmodel import select

from api.database import SessionDep
from api.portfolio.models import Certification, CertificationCreate, CertificationUpdate, CertificationReadComplete, Tag
from api.security import validate_api_key

router = APIRouter()


@router.post("/certification", status_code=status.HTTP_201_CREATED, dependencies=[Depends(validate_api_key)])
async def create_certification(certification_data: CertificationCreate, db: SessionDep):
    new_certification = Certification.model_validate(certification_data.model_dump())
    db.add(new_certification)
    db.commit()
    db.refresh(new_certification)
    return new_certification


@router.get("/certification/{certification_id}", response_model=Certification)
async def get_certification(certification_id: int, db: SessionDep):
    certification = db.get(Certification, certification_id)
    if not certification:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Certification with id {certification_id} not found")
    return certification


@router.get("/certifications", response_model=List[CertificationReadComplete])
def get_certifications(
        db: SessionDep,
        year: Optional[int] = Query(None, description="Filtrar por Año de emisión"),
        academy_id: Optional[int] = Query(None, description="Filtrar por ID de Academia"),
        category_id: Optional[int] = Query(None, description="Filtrar por ID de Categoría"),
        tag_id: Optional[int] = Query(None, description="Filtrar por ID de Tecnología (Tag)"),
        offset: int = 0,
        limit: int = Query(default=10)
):
    query = select(Certification)

    if year:
        query = query.where(Certification.year == year)

    if academy_id:
        query = query.where(Certification.academy_id == academy_id)

    if category_id:
        query = query.where(Certification.category_id == category_id)

    if tag_id:
        query = query.where(Certification.tags.any(Tag.id == tag_id))

    query = query.options(
        selectinload(Certification.academy),
        selectinload(Certification.category),
        selectinload(Certification.translations),
        selectinload(Certification.tags)
    )

    return db.exec(query.offset(offset).limit(limit)).all()


@router.patch("/certification/{certification_id}", response_model=Certification,
              dependencies=[Depends(validate_api_key)])
async def update_certification(certification_id: int, certification_data: CertificationUpdate, db: SessionDep):
    certification = db.get(Certification, certification_id)
    if not certification:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Certification with id {certification_id} not found")

    update_dic = certification_data.model_dump(exclude_unset=True)
    for key, value in update_dic.items():
        setattr(certification, key, value)

    db.add(certification)
    db.commit()
    db.refresh(certification)
    return certification


@router.delete("/certification/{certification_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(validate_api_key)])
async def delete_certification(certification_id: int, db: SessionDep):
    certification = db.get(Certification, certification_id)
    if not certification:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Certification with id {certification_id} not found")
    db.delete(certification)
    db.commit()
    return None
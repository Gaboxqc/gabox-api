from typing import List, Optional
from fastapi import APIRouter, status, HTTPException, Query, Depends
from sqlmodel import select

from api.database import SessionDep
from api.portfolio.models import Certification, CertificationCreate, CertificationUpdate
from api.security import validate_api_key

router = APIRouter(tags=["Certifications"])


@router.post("/certification", status_code=status.HTTP_201_CREATED, dependencies=[Depends(validate_api_key)])
async def create_certification(certification_data: CertificationCreate, db: SessionDep):
    new_certification = Certification.model_validate(certification_data.model_dump())
    db.add(new_certification)
    db.commit()
    db.refresh(new_certification)
    return new_certification


@router.get("/certification", response_model=List[Certification])
async def list_certifications(db: SessionDep, offset: int = 0, limit: int = Query(default=10)):
    return db.exec(select(Certification).offset(offset).limit(limit)).all()


@router.get("/certification/{certification_id}", response_model=Certification)
async def get_certification(certification_id: int, db: SessionDep):
    certification = db.get(Certification, certification_id)
    if not certification:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Certification with id {certification_id} not found")
    return certification


@router.get("/certification_by", response_model=List[Certification])
async def get_certifications_by(
        db: SessionDep,
        category_id: Optional[int] = Query(None, description="Filtrar por ID de Categoría"),
        academy_id: Optional[int] = Query(None, description="Filtrar por ID de Academia/Emisor"),
):
    query = select(Certification)

    if category_id:
        query = query.where(Certification.category_id == category_id)

    if academy_id:
        query = query.where(Certification.academy_id == academy_id)

    results = db.exec(query).all()
    return results


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
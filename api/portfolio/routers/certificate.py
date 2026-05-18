from typing import List
from fastapi import APIRouter, status, HTTPException
from sqlmodel import select

from api.database import SessionDep
from api.portfolio.models import Certificate, CertificateCreate, CertificateUpdate

router = APIRouter()

@router.post("/certificate", status_code=status.HTTP_201_CREATED)
async def create_certificate(certificate_data: CertificateCreate, db: SessionDep):
    new_certificate = Certificate.model_validate(certificate_data.model_dump())
    db.add(new_certificate)
    db.commit()
    db.refresh(new_certificate)
    return new_certificate

@router.get("/certificate", response_model=List[Certificate])
async def list_certificate(db: SessionDep):
    return db.exec(select(Certificate)).all()

@router.get("/certificate/{certificate_id}", response_model=Certificate)
async def get_certificate(certificate_id: int, db: SessionDep):
    certificate = db.get(Certificate, certificate_id)
    if not certificate:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Certificate with id {certificate_id} not found")
    return certificate

@router.patch("/certificate/{certificate_id}", response_model=Certificate)
async def update_certificate(certificate_id: int, certificate_data: CertificateUpdate, db: SessionDep):
    certificate = db.get(Certificate, certificate_id)
    if not certificate:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Certificate with id {certificate_id} not found")
    update_dic = certificate_data.model_dump(exclude_unset=True)
    for key, value in update_dic.items():
        setattr(certificate, key, value)

    db.add(certificate)
    db.commit()
    db.refresh(certificate)
    return certificate

@router.delete("/certificate/{certificate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_certificate(certificate_id: int, db: SessionDep):
    certificate = db.get(Certificate, certificate_id)
    if not certificate:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Certificate with id {certificate_id} not found")
    db.delete(certificate)
    db.commit()
    return None
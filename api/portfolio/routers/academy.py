from typing import List
from fastapi import APIRouter, status, HTTPException
from sqlmodel import select

from api.database import SessionDep
from api.portfolio.models import Academy, AcademyCreate, AcademyUpdate

router = APIRouter()

@router.post("/academy", status_code=status.HTTP_201_CREATED)
async def create_academy(academy_data: AcademyCreate, db: SessionDep):
    new_academy = Academy.model_validate(academy_data.model_dump())
    db.add(new_academy)
    db.commit()
    db.refresh(new_academy)
    return new_academy

@router.get("/academy", response_model=List[Academy])
async def list_academy(db: SessionDep):
    return db.exec(select(Academy)).all()

@router.get("/academy/{academy_id}", response_model=Academy)
async def get_academy(academy_id: int, db: SessionDep):
    academy = db.get(Academy, academy_id)
    if not academy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Academy with id {academy_id} not found")
    return academy

@router.patch("/academy/{academy_id}", response_model=Academy)
async def update_academy(academy_id: int, academy_data: AcademyUpdate, db: SessionDep):
    academy = db.get(Academy, academy_id)
    if not academy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Academy with id {academy_id} not found")
    update_dic = academy_data.model_dump(exclude_unset=True)
    for key, value in update_dic.items():
        setattr(academy, key, value)
    db.add(academy)
    db.commit()
    db.refresh(academy)
    return academy

@router.delete("/academy/{academy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_academy(academy_id: int, db: SessionDep):
    academy = db.get(Academy, academy_id)
    if not academy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Academy with id {academy_id} not found")
    db.delete(academy)
    db.commit()
    return None
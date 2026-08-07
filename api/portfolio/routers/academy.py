from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select

from api.core.database import SessionDep
from api.portfolio.models import Academy, AcademyCreate, AcademyRead, AcademyUpdate
from api.core.security import validate_api_key

router = APIRouter()


@router.post(
    "/academies",
    response_model=AcademyRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(validate_api_key)],
)
async def create_academy(academy_data: AcademyCreate, db: SessionDep):
    new_academy = Academy.model_validate(academy_data.model_dump())
    db.add(new_academy)
    db.commit()
    db.refresh(new_academy)
    return new_academy


@router.get("/academies", response_model=list[AcademyRead])
async def list_academies(db: SessionDep):
    return db.exec(select(Academy)).all()


@router.get("/academies/{academy_id}", response_model=AcademyRead)
async def get_academy(academy_id: int, db: SessionDep):
    academy = db.get(Academy, academy_id)
    if not academy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Academy with id {academy_id} not found",
        )
    return academy


@router.patch(
    "/academies/{academy_id}",
    response_model=AcademyRead,
    dependencies=[Depends(validate_api_key)],
)
async def update_academy(academy_id: int, academy_data: AcademyUpdate, db: SessionDep):
    academy = db.get(Academy, academy_id)
    if not academy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Academy with id {academy_id} not found",
        )
    update_data = academy_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(academy, key, value)
    db.add(academy)
    db.commit()
    db.refresh(academy)
    return academy


@router.delete(
    "/academies/{academy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(validate_api_key)],
)
async def delete_academy(academy_id: int, db: SessionDep):
    academy = db.get(Academy, academy_id)
    if not academy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Academy with id {academy_id} not found",
        )
    db.delete(academy)
    db.commit()
    return None

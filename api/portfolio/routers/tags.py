from typing import List
from fastapi import APIRouter, status, HTTPException
from sqlmodel import select

from api.database import SessionDep
from api.portfolio.models import Tag, TagCreate, TagUpdate

router = APIRouter()

@router.post("/tag", response_model=Tag, status_code=status.HTTP_201_CREATED)
async def create_tag(tag_data: TagCreate, db: SessionDep):
    new_tag = Tag.model_validate(tag_data.model_dump())
    db.add(new_tag)
    db.commit()
    db.refresh(new_tag)
    return new_tag

@router.get("/tag", response_model=List[Tag])
async def list_tags(db: SessionDep):
    return db.exec(select(Tag)).all()

@router.get("/tag/{tag_id}", response_model=Tag)
async def get_tag(tag_id: int, db: SessionDep):
    tag = db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag no found")
    return tag

@router.patch("/tag/{tag_id}", response_model=Tag)
async def update_tag(tag_id: int, tag_data: TagUpdate, db: SessionDep):
    tag = db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Product with id {tag_id} not found")
    update_dict = tag_data.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(tag, key, value)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag

@router.delete("/tag/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(tag_id: int, db: SessionDep):
    tag = db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag no found")
    db.delete(tag)
    db.commit()
    return {"detail": "ok"}
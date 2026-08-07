from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select

from api.core.database import SessionDep
from api.portfolio.models import Tag, TagCreate, TagRead, TagUpdate
from api.core.security import validate_api_key

router = APIRouter()


@router.post(
    "/tags",
    response_model=TagRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(validate_api_key)],
)
async def create_tag(tag_data: TagCreate, db: SessionDep):
    new_tag = Tag.model_validate(tag_data.model_dump())
    db.add(new_tag)
    db.commit()
    db.refresh(new_tag)
    return new_tag


@router.get("/tags", response_model=list[TagRead])
async def list_tags(db: SessionDep):
    return db.exec(select(Tag)).all()


@router.get("/tags/{tag_id}", response_model=TagRead)
async def get_tag(tag_id: int, db: SessionDep):
    tag = db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Tag with id {tag_id} not found"
        )
    return tag


@router.patch(
    "/tags/{tag_id}",
    response_model=TagRead,
    dependencies=[Depends(validate_api_key)],
)
async def update_tag(tag_id: int, tag_data: TagUpdate, db: SessionDep):
    tag = db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Tag with id {tag_id} not found"
        )
    update_data = tag_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(tag, key, value)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


@router.delete(
    "/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(validate_api_key)],
)
async def delete_tag(tag_id: int, db: SessionDep):
    tag = db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Tag with id {tag_id} not found"
        )
    db.delete(tag)
    db.commit()
    return None

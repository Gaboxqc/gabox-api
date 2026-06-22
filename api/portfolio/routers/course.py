from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import joinedload, selectinload
from sqlmodel import select

from api.database import SessionDep
from api.portfolio.models import Course, CourseCreate, CourseReadComplete, CourseTag, CourseUpdate
from api.security import validate_api_key

router = APIRouter()


def _load_course(course_id: int, db: SessionDep) -> Any | None:
    course = db.exec(
        select(Course)
        .where(Course.id == course_id)
        .options(
            joinedload(Course.academy),
            joinedload(Course.category),
            selectinload(Course.tags),
            selectinload(Course.translations),
        )
    ).first()
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Course with id {course_id} not found",
        )
    return course


@router.post(
    "/courses",
    response_model=CourseReadComplete,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(validate_api_key)],
)
async def create_course(course_data: CourseCreate, db: SessionDep):
    new_course = Course.model_validate(course_data.model_dump())
    db.add(new_course)
    db.commit()
    db.refresh(new_course)
    return _load_course(new_course.id, db)


@router.get("/courses/{course_id}", response_model=CourseReadComplete)
async def get_course(course_id: int, db: SessionDep):
    return _load_course(course_id, db)


@router.get("/courses", response_model=list[CourseReadComplete])
async def get_courses(
    db: SessionDep,
    category_id: Annotated[list[int] | None, Query(description="Filter by category ID")] = None,
    tag_id: Annotated[list[int] | None, Query(description="Filter by tag ID")] = None,
    offset: int = 0,
    limit: Annotated[int, Query(le=100)] = 10,
):
    if category_id is None:
        category_id = []
    if tag_id is None:
        tag_id = []

    query = select(Course).distinct()

    if category_id:
        query = query.where(Course.category_id.in_(category_id))

    if tag_id:
        query = query.where(
            Course.id.in_(select(CourseTag.course_id).where(CourseTag.tag_id.in_(tag_id)))
        )

    query = query.options(
        joinedload(Course.academy),
        joinedload(Course.category),
        selectinload(Course.tags),
        selectinload(Course.translations),
    )

    return db.exec(query.offset(offset).limit(limit)).all()


@router.patch(
    "/courses/{course_id}",
    response_model=CourseReadComplete,
    dependencies=[Depends(validate_api_key)],
)
async def update_course(course_id: int, course_data: CourseUpdate, db: SessionDep):
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Course with id {course_id} not found",
        )
    update_data = course_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(course, key, value)
    db.add(course)
    db.commit()
    return _load_course(course_id, db)


@router.delete(
    "/courses/{course_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(validate_api_key)],
)
async def delete_course(course_id: int, db: SessionDep):
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Course with id {course_id} not found",
        )
    db.delete(course)
    db.commit()
    return None

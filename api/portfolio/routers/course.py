from typing import List, Optional
from fastapi import APIRouter, status, HTTPException, Query, Depends
from sqlalchemy.orm import joinedload, selectinload
from sqlmodel import select

from api.database import SessionDep
from api.portfolio.models import Course, CourseCreate, CourseUpdate, CourseTag, CourseReadComplete
from api.security import validate_api_key

router = APIRouter()


@router.post("/courses", response_model=Course, status_code=status.HTTP_201_CREATED, dependencies=[Depends(validate_api_key)])
async def create_course(course_data: CourseCreate, db: SessionDep):
    new_course = Course.model_validate(course_data.model_dump())
    db.add(new_course)
    db.commit()
    db.refresh(new_course)
    return new_course


@router.get("/courses", response_model=List[CourseReadComplete])
async def get_courses_by(
        db: SessionDep,
        category_id: List[int] = Query(default=[], description="Filter by category"),
        tag_id: List[int] = Query(default=[], description="Filter by tag"),
        offset: int = 0,
        limit: int = Query(default=10, le=100)
):
    query = select(Course).distinct()  # distinct here instead of .unique() at the end

    if category_id:
        query = query.where(Course.category_id.in_(category_id))

    if tag_id:
        query = query.where(
            Course.id.in_(
                select(CourseTag.course_id)
                .where(CourseTag.tag_id.in_(tag_id))
            )
        )

    query = query.options(
        joinedload(Course.academy),
        joinedload(Course.category),
        selectinload(Course.tags),
        selectinload(Course.translations)
    )

    results = db.exec(query.offset(offset).limit(limit)).all()
    return results


@router.patch("/courses/{course_id}", response_model=Course, dependencies=[Depends(validate_api_key)])
async def update_course(course_id: int, course_data: CourseUpdate, db: SessionDep):
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Course with id {course_id} not found")

    update_dic = course_data.model_dump(exclude_unset=True)
    for key, value in update_dic.items():
        setattr(course, key, value)

    db.add(course)
    db.commit()
    db.refresh(course)
    return course


@router.delete("/courses/{course_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(validate_api_key)])
async def delete_course(course_id: int, db: SessionDep):
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Course with id {course_id} not found")
    db.delete(course)
    db.commit()
    return None

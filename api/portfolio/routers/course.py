from typing import List, Optional
from fastapi import APIRouter, status, HTTPException, Query, Depends
from sqlmodel import select

from api.database import SessionDep
from api.portfolio.models import Course, CourseCreate, CourseUpdate, CourseTag
from api.security import validate_api_key

router = APIRouter()


@router.post("/course", status_code=status.HTTP_201_CREATED, dependencies=[Depends(validate_api_key)])
async def create_course(course_data: CourseCreate, db: SessionDep):
    new_course = Course.model_validate(course_data.model_dump())
    db.add(new_course)
    db.commit()
    db.refresh(new_course)
    return new_course


@router.get("/course", response_model=List[Course])
async def list_courses(db: SessionDep, offset: int = 0, limit: int = Query(default=10)):
    return db.exec(select(Course).offset(offset).limit(limit)).all()


@router.get("/course/{course_id}", response_model=Course)
async def get_course(course_id: int, db: SessionDep):
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Course with id {course_id} not found")
    return course


@router.get("/course_by", response_model=List[Course])
async def get_courses_by(
        db: SessionDep,
        category_id: Optional[int] = Query(None, description="Filtrar por ID de Categoría"),
        tag_id: Optional[int] = Query(None, description="Filtrar por ID de Tag (Lenguaje/Framework)"),
):
    query = select(Course)

    if category_id:
        query = query.where(Course.category_id == category_id)

    if tag_id:
        query = (
            query
            .join(CourseTag)
            .where(CourseTag.tag_id == tag_id)
        )

    results = db.exec(query).all()
    return results


@router.patch("/course/{course_id}", response_model=Course, dependencies=[Depends(validate_api_key)])
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


@router.delete("/course/{course_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(validate_api_key)])
async def delete_course(course_id: int, db: SessionDep):
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Course with id {course_id} not found")
    db.delete(course)
    db.commit()
    return None

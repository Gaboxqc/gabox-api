from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlmodel import select

from api.core.auth.deps import require_admin
from api.core.database import SessionDep
from api.core.deps import PageDep, eager_options, get_or_404, get_with_relations
from api.portfolio.deps import apply_tag_ids, count_matching, resolve_tags, set_total_count
from api.portfolio.models import Course, CourseCreate, CourseReadComplete, CourseUpdate, Tag

router = APIRouter(prefix="/courses")
authenticated = [Depends(require_admin)]

RELATIONS = (
    Course.academy,
    Course.category,
    Course.tags,
    Course.translations,
)


def _load(db: SessionDep, course_id: int) -> Course:
    return get_with_relations(db, Course, course_id, RELATIONS)


@router.post(
    "",
    response_model=CourseReadComplete,
    status_code=status.HTTP_201_CREATED,
    dependencies=authenticated,
)
async def create_course(course_data: CourseCreate, db: SessionDep):
    payload = course_data.model_dump()
    tag_ids = payload.pop("tag_ids", [])

    course = Course.model_validate(payload)
    course.tags = resolve_tags(db, tag_ids)
    db.add(course)
    db.commit()
    db.refresh(course)
    return _load(db, course.id)


@router.get("", response_model=list[CourseReadComplete])
async def list_courses(
    response: Response,
    db: SessionDep,
    page: PageDep,
    category_id: Annotated[list[int] | None, Query(description="Filter by category ID")] = None,
    tag_id: Annotated[list[int] | None, Query(description="Filter by tag ID")] = None,
):
    query = select(Course)

    if category_id:
        query = query.where(Course.category_id.in_(category_id))

    if tag_id:
        query = query.where(Course.tags.any(Tag.id.in_(tag_id)))

    set_total_count(response, count_matching(db, query))

    query = query.options(*eager_options(RELATIONS)).order_by(Course.id)
    return db.exec(query.offset(page.offset).limit(page.limit)).all()


@router.get("/{course_id}", response_model=CourseReadComplete)
async def get_course(course_id: int, db: SessionDep):
    return _load(db, course_id)


@router.patch(
    "/{course_id}",
    response_model=CourseReadComplete,
    dependencies=authenticated,
)
async def update_course(course_id: int, course_data: CourseUpdate, db: SessionDep):
    course = get_or_404(db, Course, course_id)
    changes = course_data.model_dump(exclude_unset=True)
    tag_ids = changes.pop("tag_ids", None)

    for field, value in changes.items():
        setattr(course, field, value)
    apply_tag_ids(db, course, tag_ids)

    db.add(course)
    db.commit()
    return _load(db, course_id)


@router.delete(
    "/{course_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=authenticated,
)
async def delete_course(course_id: int, db: SessionDep):
    db.delete(get_or_404(db, Course, course_id))
    db.commit()

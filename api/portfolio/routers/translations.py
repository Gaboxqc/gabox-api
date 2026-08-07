"""Translation sub-resources.

Projects, courses and certifications each carry per-language content keyed by
(parent id, language code). The three routers were identical apart from names,
so they share one factory.

Note the response models: these endpoints previously returned the *table*
classes, which exposed the internal `project_id` / `course_id` /
`certification_id` foreign keys. They now return the `...Read` schemas that
already existed in models.py but were never wired up.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import SQLModel, select

from api.core.database import SessionDep
from api.core.deps import PageDep, get_or_404
from api.core.security import validate_api_key
from api.portfolio.models import (
    Certification,
    CertificationTranslation,
    CertificationTranslationCreate,
    CertificationTranslationRead,
    CertificationTranslationUpdate,
    Course,
    CourseTranslation,
    CourseTranslationCreate,
    CourseTranslationRead,
    CourseTranslationUpdate,
    Project,
    ProjectTranslation,
    ProjectTranslationCreate,
    ProjectTranslationRead,
    ProjectTranslationUpdate,
)


def translation_router[ModelT: SQLModel](
    *,
    parent_model: type[SQLModel],
    model: type[ModelT],
    create_schema: type[SQLModel],
    read_schema: type[SQLModel],
    update_schema: type[SQLModel],
    parent_field: str,
    prefix: str,
    tag: str,
    resource: str,
) -> APIRouter:
    """Build the five translation routes for one parent resource.

    `parent_field` is the foreign-key attribute on the translation model, e.g.
    `"project_id"`; `resource` is the singular parent name used in operation
    ids and error messages.
    """
    router = APIRouter(prefix=prefix, tags=[tag])
    authenticated = [Depends(validate_api_key)]
    parent_column = getattr(model, parent_field)

    def _translation_or_404(db: SessionDep, parent_id: int, language_code: str) -> ModelT:
        translation = db.exec(
            select(model).where(
                parent_column == parent_id,
                model.language_code == language_code,
            )
        ).first()
        if translation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Translation for {resource} {parent_id} in '{language_code}' not found",
            )
        return translation

    @router.post(
        "/{parent_id}/translations",
        response_model=read_schema,
        status_code=status.HTTP_201_CREATED,
        dependencies=authenticated,
        operation_id=f"create_{resource}_translation",
        summary=f"Add a translation to a {resource}",
    )
    async def create(parent_id: int, translation_data: create_schema, db: SessionDep):
        # The parent is addressed by URL, so its absence is a 404 rather than
        # the 422 the foreign-key constraint would otherwise produce.
        get_or_404(db, parent_model, parent_id)
        # A duplicate (parent, language) hits the composite primary key and the
        # global handler turns it into a 409 — no read-then-write race here.
        translation = model.model_validate(
            {**translation_data.model_dump(), parent_field: parent_id}
        )
        db.add(translation)
        db.commit()
        db.refresh(translation)
        return translation

    @router.get(
        "/{parent_id}/translations",
        response_model=list[read_schema],
        operation_id=f"list_{resource}_translations",
        summary=f"List a {resource}'s translations",
    )
    async def list_all(parent_id: int, db: SessionDep, page: PageDep):
        get_or_404(db, parent_model, parent_id)
        query = (
            select(model)
            .where(parent_column == parent_id)
            .order_by(model.language_code)
            .offset(page.offset)
            .limit(page.limit)
        )
        return db.exec(query).all()

    @router.get(
        "/{parent_id}/translations/{language_code}",
        response_model=read_schema,
        operation_id=f"get_{resource}_translation",
        summary=f"Get one of a {resource}'s translations",
    )
    async def get_one(parent_id: int, language_code: str, db: SessionDep):
        return _translation_or_404(db, parent_id, language_code)

    @router.patch(
        "/{parent_id}/translations/{language_code}",
        response_model=read_schema,
        dependencies=authenticated,
        operation_id=f"update_{resource}_translation",
        summary=f"Update one of a {resource}'s translations",
    )
    async def update(
        parent_id: int,
        language_code: str,
        translation_data: update_schema,
        db: SessionDep,
    ):
        translation = _translation_or_404(db, parent_id, language_code)
        for field, value in translation_data.model_dump(exclude_unset=True).items():
            setattr(translation, field, value)
        db.add(translation)
        db.commit()
        db.refresh(translation)
        return translation

    @router.delete(
        "/{parent_id}/translations/{language_code}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=authenticated,
        operation_id=f"delete_{resource}_translation",
        summary=f"Delete one of a {resource}'s translations",
    )
    async def delete(parent_id: int, language_code: str, db: SessionDep):
        db.delete(_translation_or_404(db, parent_id, language_code))
        db.commit()

    return router


project_translation_router = translation_router(
    parent_model=Project,
    model=ProjectTranslation,
    create_schema=ProjectTranslationCreate,
    read_schema=ProjectTranslationRead,
    update_schema=ProjectTranslationUpdate,
    parent_field="project_id",
    prefix="/projects",
    tag="Portfolio: Project Translations",
    resource="project",
)

course_translation_router = translation_router(
    parent_model=Course,
    model=CourseTranslation,
    create_schema=CourseTranslationCreate,
    read_schema=CourseTranslationRead,
    update_schema=CourseTranslationUpdate,
    parent_field="course_id",
    prefix="/courses",
    tag="Portfolio: Course Translations",
    resource="course",
)

certification_translation_router = translation_router(
    parent_model=Certification,
    model=CertificationTranslation,
    create_schema=CertificationTranslationCreate,
    read_schema=CertificationTranslationRead,
    update_schema=CertificationTranslationUpdate,
    parent_field="certification_id",
    prefix="/certifications",
    tag="Portfolio: Certification Translations",
    resource="certification",
)

translation_routers = [
    project_translation_router,
    course_translation_router,
    certification_translation_router,
]

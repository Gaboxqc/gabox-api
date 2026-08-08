"""Generic CRUD router for simple lookup resources.

Tags, academies, categories, project types, difficulty levels and languages are
all the same shape: a primary key and a name. They had drifted into six
different API surfaces — some paginated, some not, only two with PATCH or
GET-by-id. This factory gives them one identical contract.

Resources with real query logic (projects, certifications, courses) stay
hand-written; forcing them through here would cost more than it saves.
"""

from fastapi import APIRouter, Depends, status
from sqlmodel import SQLModel, select

from api.core.auth.deps import require_admin
from api.core.database import SessionDep
from api.core.deps import PageDep, get_or_404, primary_key_column


def crud_router[ModelT: SQLModel](
    *,
    model: type[ModelT],
    create_schema: type[SQLModel],
    read_schema: type[SQLModel],
    update_schema: type[SQLModel],
    prefix: str,
    tag: str,
    resource: str,
    pk_type: type = int,
) -> APIRouter:
    """Build the five standard CRUD routes for `model`.

    `resource` is the singular snake_case name used to build operation ids, so
    generated clients get `create_project_type` rather than a collision between
    six identically-named closures.
    """
    router = APIRouter(prefix=prefix, tags=[tag])
    pk_column = primary_key_column(model)
    plural = prefix.strip("/").replace("-", "_")
    authenticated = [Depends(require_admin)]

    @router.post(
        "",
        response_model=read_schema,
        status_code=status.HTTP_201_CREATED,
        dependencies=authenticated,
        operation_id=f"create_{resource}",
        summary=f"Create a {resource.replace('_', ' ')}",
    )
    async def create(payload: create_schema, db: SessionDep):
        instance = model.model_validate(payload.model_dump())
        db.add(instance)
        db.commit()
        db.refresh(instance)
        return instance

    @router.get(
        "",
        response_model=list[read_schema],
        operation_id=f"list_{plural}",
        summary=f"List {plural.replace('_', ' ')}",
    )
    async def list_all(db: SessionDep, page: PageDep):
        query = select(model).order_by(pk_column).offset(page.offset).limit(page.limit)
        return db.exec(query).all()

    @router.get(
        "/{item_id}",
        response_model=read_schema,
        operation_id=f"get_{resource}",
        summary=f"Get a single {resource.replace('_', ' ')}",
    )
    async def get_one(item_id: pk_type, db: SessionDep):
        return get_or_404(db, model, item_id)

    @router.patch(
        "/{item_id}",
        response_model=read_schema,
        dependencies=authenticated,
        operation_id=f"update_{resource}",
        summary=f"Update a {resource.replace('_', ' ')}",
    )
    async def update(item_id: pk_type, payload: update_schema, db: SessionDep):
        instance = get_or_404(db, model, item_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(instance, field, value)
        db.add(instance)
        db.commit()
        db.refresh(instance)
        return instance

    @router.delete(
        "/{item_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=authenticated,
        operation_id=f"delete_{resource}",
        summary=f"Delete a {resource.replace('_', ' ')}",
    )
    async def delete(item_id: pk_type, db: SessionDep):
        db.delete(get_or_404(db, model, item_id))
        db.commit()

    return router

"""Reusable route dependencies and lookup helpers."""

import re
from collections.abc import Sequence
from typing import Annotated, Any, TypeVar

from fastapi import Depends, HTTPException, Query, status
from sqlalchemy.orm import selectinload
from sqlmodel import Session, SQLModel, select

ModelT = TypeVar("ModelT", bound=SQLModel)


class Pagination:
    """Shared `offset` / `limit` query parameters."""

    def __init__(
        self,
        offset: Annotated[int, Query(ge=0, description="Records to skip")] = 0,
        limit: Annotated[int, Query(ge=1, le=100, description="Max records to return")] = 10,
    ) -> None:
        self.offset = offset
        self.limit = limit


PageDep = Annotated[Pagination, Depends()]


def _label(model: type[SQLModel]) -> str:
    """`DifficultyLevel` -> `Difficulty level`, for readable 404 messages."""
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", model.__name__)
    return spaced[0].upper() + spaced[1:].lower()


def _not_found(model: type[SQLModel], ident: Any) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{_label(model)} with id {ident} not found",
    )


def primary_key_column(model: type[SQLModel]):
    columns = list(model.__table__.primary_key.columns)
    if len(columns) != 1:
        raise TypeError(
            f"Expected a single-column primary key; {model.__name__} has {len(columns)}."
        )
    return getattr(model, columns[0].name)


def eager_options(relations: Sequence[Any]) -> list[Any]:
    """Eager-load `relations` in one extra query each, instead of N+1 lazy loads."""
    return [selectinload(relation) for relation in relations]


def get_or_404(db: Session, model: type[ModelT], ident: Any) -> ModelT:
    """Fetch by primary key or raise a 404 naming the resource."""
    instance = db.get(model, ident)
    if instance is None:
        raise _not_found(model, ident)
    return instance


def get_with_relations(
    db: Session, model: type[ModelT], ident: Any, relations: Sequence[Any]
) -> ModelT:
    """`get_or_404` for models whose read schema includes nested relationships."""
    query = (
        select(model).where(primary_key_column(model) == ident).options(*eager_options(relations))
    )
    instance = db.exec(query).first()
    if instance is None:
        raise _not_found(model, ident)
    return instance


def escape_like(term: str) -> str:
    """Neutralise `%` and `_` so a search term matches literally.

    Without this, a user searching for `100%` matches every row.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

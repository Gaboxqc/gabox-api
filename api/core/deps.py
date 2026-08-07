"""Reusable route dependencies and lookup helpers."""

import re
from typing import Annotated, Any, TypeVar

from fastapi import Depends, HTTPException, Query, status
from sqlmodel import Session, SQLModel

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


def get_or_404(db: Session, model: type[ModelT], ident: Any) -> ModelT:
    """Fetch by primary key or raise a 404 naming the resource."""
    instance = db.get(model, ident)
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{_label(model)} with id {ident} not found",
        )
    return instance

"""Portfolio-specific route helpers.

Generic, model-agnostic helpers live in `api.core.deps`; these know about the
portfolio's own tables.
"""

from typing import Any

from fastapi import HTTPException, Response, status
from sqlalchemy import Select
from sqlmodel import Session, col, func, select

from api.portfolio.models import Tag

TOTAL_COUNT_HEADER = "X-Total-Count"


def resolve_tags(db: Session, tag_ids: list[int]) -> list[Tag]:
    """Turn a list of tag ids into `Tag` rows.

    Unknown ids are rejected with a 422 naming them, rather than being dropped
    silently — a typo in a tag id should not look like a successful save that
    quietly lost a tag. Duplicates are collapsed, since the link table's
    composite primary key would otherwise raise on the second copy.
    """
    if not tag_ids:
        return []

    # dict.fromkeys rather than set() so the reported order stays predictable.
    unique_ids = list(dict.fromkeys(tag_ids))
    tags = db.exec(select(Tag).where(col(Tag.id).in_(unique_ids))).all()

    found = {tag.id for tag in tags}
    missing = [tag_id for tag_id in unique_ids if tag_id not in found]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown tag id(s): {', '.join(str(i) for i in missing)}.",
        )

    return list(tags)


def apply_tag_ids(db: Session, instance: Any, tag_ids: list[int] | None) -> None:
    """Replace an instance's tags, or leave them untouched when `tag_ids` is None.

    `None` and `[]` mean different things here: the first is "this request did
    not mention tags", the second is "remove every tag".
    """
    if tag_ids is None:
        return
    instance.tags = resolve_tags(db, tag_ids)


def count_matching(db: Session, query: Select) -> int:
    """Total rows a filtered query would return, ignoring offset and limit.

    Counting the query's own subquery means the filters cannot drift out of step
    with the ones used to fetch the page. Call this *before* attaching eager-load
    options or ordering, neither of which belongs in a count.
    """
    return int(db.exec(select(func.count()).select_from(query.subquery())).one())


def set_total_count(response: Response, total: int) -> None:
    """Publish the unpaginated total so a client can size its pager.

    A header rather than a `{items, total}` envelope: the public frontend already
    consumes bare arrays, and changing the body shape would break it.
    `expose_headers` in the CORS config is what makes this readable from the
    browser — without it the header arrives but JavaScript cannot see it.
    """
    response.headers[TOTAL_COUNT_HEADER] = str(total)

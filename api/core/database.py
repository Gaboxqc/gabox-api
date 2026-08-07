"""Engine and session management.

Alembic owns the schema — nothing here creates tables. Run `alembic upgrade
head` to migrate.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.pool import NullPool

# SQLModel is re-exported so Alembic imports the metadata from one place.
from sqlmodel import Session, SQLModel, create_engine  # noqa: F401

from api.core.config import settings


def _pool_options() -> dict:
    """Serverless invocations are single-use processes, so a pool would only
    hold connections open that the next invocation can never reuse. Long-lived
    processes (local uvicorn, a container) do want one."""
    if settings.is_serverless:
        return {"poolclass": NullPool}
    return {"pool_pre_ping": True, "pool_recycle": 300}


engine = create_engine(settings.database_url, echo=settings.sql_echo, **_pool_options())


def get_session():
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]

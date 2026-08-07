"""Shared test fixtures.

Every test runs against a fresh in-memory SQLite database. The environment is
pinned *before* the app is imported, because `api.core.config` builds its
Settings at import time — and pinning it unconditionally (rather than with
setdefault) guarantees a stray DATABASE_URL in the developer's shell can never
point a test run at a real database.
"""

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["API_MASTER_KEY"] = "test-master-key"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import api.portfolio.models  # noqa: F401,E402  registers tables on the metadata
from api.core.config import settings  # noqa: E402
from api.core.database import get_session  # noqa: E402
from main import app  # noqa: E402


@pytest.fixture(name="engine")
def engine_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # SQLite ignores foreign keys unless asked; without this the tests would
    # not exercise the constraint behaviour that PostgreSQL enforces.
    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(name="client")
def client_fixture(engine):
    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(name="auth")
def auth_fixture() -> dict[str, str]:
    return {"X-API-KEY": settings.api_master_key}


@pytest.fixture(name="create")
def create_fixture(client, auth):
    """POST a payload and return the created body, failing loudly on error."""

    def _create(path: str, **payload):
        response = client.post(path, json=payload, headers=auth)
        assert response.status_code == 201, f"{path} -> {response.status_code} {response.text}"
        return response.json()

    return _create


@pytest.fixture(name="seed")
def seed_fixture(create) -> dict[str, int]:
    """The lookup rows most tests need before they can create anything."""
    create("/portfolio/languages", code="en", name="English")
    create("/portfolio/languages", code="es", name="Spanish")
    return {
        "project_type": create("/portfolio/project-types", name="Web")["id"],
        "difficulty_level": create("/portfolio/difficulty-levels", name="Easy")["id"],
        "academy": create("/portfolio/academies", name="Coursera")["id"],
        "category": create("/portfolio/categories", name="Backend")["id"],
    }


@pytest.fixture(name="project")
def project_fixture(create, seed) -> int:
    return create(
        "/portfolio/projects",
        year=2025,
        project_type_id=seed["project_type"],
        difficulty_level_id=seed["difficulty_level"],
    )["id"]


@pytest.fixture(name="queries")
def queries_fixture(engine) -> list[str]:
    """Captured SQL, for asserting that eager loading avoids N+1 queries."""
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, *args):
        statements.append(statement)

    return statements

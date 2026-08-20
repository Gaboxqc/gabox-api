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
# TestClient talks to http://testserver, and httpx correctly refuses to send a
# Secure cookie over plain http to a non-localhost host. Without this the
# session cookie would be set and then never returned.
os.environ["SESSION_COOKIE_SECURE"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import api.core.audit  # noqa: F401,E402  registers tables on the metadata
import api.core.auth.models  # noqa: F401,E402  registers tables on the metadata
import api.portfolio.models  # noqa: F401,E402  registers tables on the metadata
import api.statpitch.accounts  # noqa: F401,E402  registers tables on the metadata
import api.statpitch.models  # noqa: F401,E402  registers tables on the metadata
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

    # The audit middleware opens its own session and so cannot go through
    # dependency_overrides; point its factory at the test engine too, or it
    # would write to the real one.
    import api.core.audit as audit

    original_audit_session = audit.audit_session
    audit.audit_session = lambda: Session(engine)

    with TestClient(app) as test_client:
        yield test_client

    audit.audit_session = original_audit_session
    app.dependency_overrides.clear()


@pytest.fixture(name="auth")
def auth_fixture() -> dict[str, str]:
    return {"X-API-KEY": settings.api_master_key}


ADMIN_USERNAME = "gabox"
ADMIN_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(name="admin")
def admin_fixture(engine):
    """An active admin account with a known password."""
    from api.core.auth.models import AdminUser
    from api.core.auth.passwords import hash_password

    with Session(engine) as db:
        user = AdminUser(username=ADMIN_USERNAME, password_hash=hash_password(ADMIN_PASSWORD))
        db.add(user)
        db.commit()
        db.refresh(user)
        return {"id": user.id, "username": user.username, "password": ADMIN_PASSWORD}


@pytest.fixture(name="login")
def login_fixture(client, admin):
    """Log in and return the CSRF header the session expects.

    TestClient keeps the session cookie in its jar automatically; only the CSRF
    token has to be moved into a header by hand, exactly as the dashboard does.
    """

    def _login(username=None, password=None):
        response = client.post(
            "/auth/login",
            json={
                "username": username or admin["username"],
                "password": password or admin["password"],
            },
        )
        assert response.status_code == 200, f"login failed: {response.text}"
        return {"X-CSRF-Token": client.cookies["gabox_csrf"]}

    return _login


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


@pytest.fixture(name="make_fixture")
def make_fixture_factory():
    """Build a `StatPitchFixture` with plausible defaults.

    Probabilities are internally consistent (the 1X2 three sum to 1), so a test
    that only cares about odds does not have to restate the prediction.
    """
    from api.statpitch.clock import today_local
    from api.statpitch.models import StatPitchFixture

    counter = {"n": 0}

    def _make(**overrides):
        counter["n"] += 1
        today = today_local()
        defaults = {
            "fixture_id": f"ESP.LALIGA|2026-2027|Home {counter['n']}|Away {counter['n']}",
            "competition_id": "ESP.LALIGA",
            "match_date": today,
            "source_date": today,
            "home_team": f"Home {counter['n']}",
            "away_team": f"Away {counter['n']}",
            "model_version": "goals-test-0001",
            "home_xg": 1.8,
            "away_xg": 1.0,
            "home_win_prob": 0.55,
            "draw_prob": 0.25,
            "away_win_prob": 0.20,
            "over_1_5": 0.78,
            "over_2_5": 0.55,
            "over_3_5": 0.32,
            "btts_yes": 0.57,
            "btts_no": 0.43,
        }
        defaults.update(overrides)
        return StatPitchFixture(**defaults)

    return _make


@pytest.fixture(name="seed_fixtures")
def seed_fixtures_factory(engine, make_fixture):
    """Persist fixtures and return them, refreshed."""
    from sqlmodel import Session

    def _seed(*fixtures):
        with Session(engine) as db:
            for fixture in fixtures:
                db.add(fixture)
            db.commit()
            for fixture in fixtures:
                db.refresh(fixture)
        return list(fixtures)

    return _seed


@pytest.fixture(name="queries")
def queries_fixture(engine) -> list[str]:
    """Captured SQL, for asserting that eager loading avoids N+1 queries."""
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, *args):
        statements.append(statement)

    return statements

"""Index and readiness endpoints."""

import pytest
from sqlalchemy.exc import OperationalError

from api.core.database import get_session
from main import app


def test_index_lists_the_projects(client):
    body = client.get("/").json()
    assert body["status"] == "online"
    assert body["projects"]["portfolio"] == "/portfolio"


def test_health_is_ok_when_the_database_responds(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_is_503_when_the_database_is_unreachable(client):
    """A reachable app with an unreachable database must not report healthy."""

    class BrokenSession:
        def execute(self, *args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    app.dependency_overrides[get_session] = lambda: BrokenSession()
    try:
        response = client.get("/health")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unreachable"}


@pytest.mark.parametrize("path", ["/", "/health"])
def test_health_endpoints_are_public(client, path):
    assert client.get(path).status_code in (200, 503)

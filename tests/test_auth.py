"""API-key authentication on write endpoints."""

import pytest

WRITE_ENDPOINTS = [
    ("post", "/portfolio/tags", {"name": "Rust"}),
    ("patch", "/portfolio/tags/1", {"name": "Rust"}),
    ("delete", "/portfolio/tags/1", None),
    ("post", "/portfolio/projects", {"year": 2025, "project_type_id": 1, "difficulty_level_id": 1}),
]


def call(client, method, path, payload, headers=None):
    """`TestClient.delete()` takes no `json=`, so go through `request()`."""
    kwargs = {"json": payload} if payload is not None else {}
    return client.request(method.upper(), path, headers=headers, **kwargs)


@pytest.mark.parametrize(("method", "path", "payload"), WRITE_ENDPOINTS)
def test_write_without_key_is_rejected(client, method, path, payload):
    response = call(client, method, path, payload)
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing API key."


@pytest.mark.parametrize(("method", "path", "payload"), WRITE_ENDPOINTS)
def test_write_with_wrong_key_is_rejected(client, method, path, payload):
    response = call(client, method, path, payload, headers={"X-API-KEY": "nope"})
    assert response.status_code == 401


def test_missing_and_wrong_key_are_indistinguishable(client):
    """Both must be 401; a 403 for one of them would reveal which failed."""
    missing = client.post("/portfolio/tags", json={"name": "Rust"})
    wrong = client.post("/portfolio/tags", json={"name": "Rust"}, headers={"X-API-KEY": "nope"})
    assert missing.status_code == wrong.status_code == 401
    assert missing.json() == wrong.json()


def test_valid_key_is_accepted(client, auth):
    assert client.post("/portfolio/tags", json={"name": "Rust"}, headers=auth).status_code == 201


@pytest.mark.parametrize(
    "path",
    ["/", "/portfolio/tags", "/portfolio/projects", "/portfolio/languages"],
)
def test_reads_are_public(client, path):
    assert client.get(path).status_code == 200

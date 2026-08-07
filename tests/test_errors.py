"""Database constraint violations must surface as client errors, not 500s."""


def test_unique_violation_is_409(client, auth):
    client.post("/portfolio/tags", json={"name": "Rust"}, headers=auth)
    response = client.post("/portfolio/tags", json={"name": "Rust"}, headers=auth)
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_unique_violation_does_not_insert(client, auth):
    client.post("/portfolio/tags", json={"name": "Rust"}, headers=auth)
    client.post("/portfolio/tags", json={"name": "Rust"}, headers=auth)
    assert len(client.get("/portfolio/tags").json()) == 1


def test_foreign_key_violation_on_write_is_422(client, auth, seed):
    response = client.post(
        "/portfolio/projects",
        json={
            "year": 2025,
            "project_type_id": 99999,
            "difficulty_level_id": seed["difficulty_level"],
        },
        headers=auth,
    )
    assert response.status_code == 422
    assert "does not exist" in response.json()["detail"]


def test_foreign_key_violation_on_delete_is_409(client, auth, project):
    """Deleting a language still referenced by a translation is a state
    conflict, not a malformed request.

    This previously raised an AssertionError from the ORM (a 500) because the
    ORM tried to null out a column that is part of a composite primary key.
    """
    client.post(
        f"/portfolio/projects/{project}/translations",
        json={"language_code": "es", "title": "Titulo", "description": "una descripcion"},
        headers=auth,
    )
    response = client.delete("/portfolio/languages/es", headers=auth)
    assert response.status_code == 409
    assert "still referenced" in response.json()["detail"]


def test_unused_language_can_still_be_deleted(client, auth, seed):
    assert client.delete("/portfolio/languages/es", headers=auth).status_code == 204


def test_error_body_does_not_leak_sql(client, auth):
    client.post("/portfolio/tags", json={"name": "Rust"}, headers=auth)
    detail = client.post("/portfolio/tags", json={"name": "Rust"}, headers=auth).json()["detail"]
    assert "INSERT" not in detail.upper()
    assert "Rust" not in detail, "the rejected value must not be echoed back"


def test_validation_error_on_bad_payload(client, auth):
    response = client.post("/portfolio/tags", json={"name": ""}, headers=auth)
    assert response.status_code == 422

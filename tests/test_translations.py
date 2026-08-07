"""Translation sub-resources for projects, courses and certifications."""

import pytest

# parent kind, create body, and the foreign key that must never be exposed.
KINDS = [
    ("projects", {"title": "Mi proyecto", "description": "una descripcion"}, "project_id"),
    ("courses", {"title": "Mi curso"}, "course_id"),
    ("certifications", {"title": "Mi certificado"}, "certification_id"),
]
ALL = pytest.mark.parametrize(("kind", "body", "foreign_key"), KINDS, ids=[k for k, *_ in KINDS])


@pytest.fixture(name="parents")
def parents_fixture(create, seed) -> dict[str, int]:
    return {
        "projects": create(
            "/portfolio/projects",
            year=2025,
            project_type_id=seed["project_type"],
            difficulty_level_id=seed["difficulty_level"],
        )["id"],
        "courses": create(
            "/portfolio/courses",
            year=2025,
            academy_id=seed["academy"],
            category_id=seed["category"],
        )["id"],
        "certifications": create(
            "/portfolio/certifications",
            year=2025,
            academy_id=seed["academy"],
            category_id=seed["category"],
        )["id"],
    }


@ALL
def test_response_does_not_leak_the_parent_foreign_key(
    client, auth, parents, kind, body, foreign_key
):
    """These endpoints used to return the table model, exposing the FK."""
    base = f"/portfolio/{kind}/{parents[kind]}/translations"
    response = client.post(base, json={**body, "language_code": "es"}, headers=auth)
    assert response.status_code == 201
    assert foreign_key not in response.json()
    assert response.json()["language_code"] == "es"


@ALL
def test_duplicate_language_is_a_conflict(client, auth, parents, kind, body, foreign_key):
    base = f"/portfolio/{kind}/{parents[kind]}/translations"
    client.post(base, json={**body, "language_code": "es"}, headers=auth)
    again = client.post(base, json={**body, "language_code": "es"}, headers=auth)
    assert again.status_code == 409


@ALL
def test_unknown_language_is_unprocessable(client, auth, parents, kind, body, foreign_key):
    base = f"/portfolio/{kind}/{parents[kind]}/translations"
    response = client.post(base, json={**body, "language_code": "zz"}, headers=auth)
    assert response.status_code == 422


@ALL
def test_missing_parent_is_404_not_422(client, auth, parents, kind, body, foreign_key):
    """The parent is named in the URL, so its absence is a 404 rather than the
    422 the foreign-key constraint would otherwise produce."""
    response = client.post(
        f"/portfolio/{kind}/99999/translations",
        json={**body, "language_code": "es"},
        headers=auth,
    )
    assert response.status_code == 404
    assert "99999" in response.json()["detail"]


@ALL
def test_list_is_ordered_and_paginated(client, auth, parents, kind, body, foreign_key):
    base = f"/portfolio/{kind}/{parents[kind]}/translations"
    for code in ("es", "en"):
        client.post(base, json={**body, "language_code": code}, headers=auth)

    listed = client.get(base).json()
    assert [row["language_code"] for row in listed] == ["en", "es"]
    assert len(client.get(f"{base}?limit=1").json()) == 1


@ALL
def test_list_for_missing_parent_is_404(client, parents, kind, body, foreign_key):
    assert client.get(f"/portfolio/{kind}/99999/translations").status_code == 404


@ALL
def test_update_and_delete(client, auth, parents, kind, body, foreign_key):
    base = f"/portfolio/{kind}/{parents[kind]}/translations"
    client.post(base, json={**body, "language_code": "es"}, headers=auth)

    updated = client.patch(f"{base}/es", json={"title": "Actualizado"}, headers=auth)
    assert updated.status_code == 200
    assert updated.json()["title"] == "Actualizado"

    assert client.delete(f"{base}/es", headers=auth).status_code == 204
    assert client.get(f"{base}/es").status_code == 404
    assert client.delete(f"{base}/es", headers=auth).status_code == 404


@ALL
def test_missing_translation_names_the_language(client, parents, kind, body, foreign_key):
    response = client.get(f"/portfolio/{kind}/{parents[kind]}/translations/zz")
    assert response.status_code == 404
    assert "'zz'" in response.json()["detail"]


def test_translations_still_nest_in_the_parent_payload(client, auth, parents):
    base = f"/portfolio/projects/{parents['projects']}/translations"
    for code in ("en", "es"):
        client.post(
            base,
            json={"language_code": code, "title": "Titulo", "description": "a description"},
            headers=auth,
        )
    body = client.get(f"/portfolio/projects/{parents['projects']}").json()
    assert len(body["translations"]) == 2

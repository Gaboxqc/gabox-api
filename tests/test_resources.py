"""Certifications and courses — filtering and nested relations."""

import pytest


@pytest.fixture(name="records")
def records_fixture(create, seed):
    other_category = create("/portfolio/categories", name="Frontend")["id"]
    return {
        "certification": create(
            "/portfolio/certifications",
            year=2024,
            academy_id=seed["academy"],
            category_id=seed["category"],
            validation_serial="ABC-123",
        )["id"],
        "course": create(
            "/portfolio/courses",
            year=2024,
            academy_id=seed["academy"],
            category_id=seed["category"],
        )["id"],
        "other_course": create(
            "/portfolio/courses",
            year=2023,
            academy_id=seed["academy"],
            category_id=other_category,
        )["id"],
        "academy": seed["academy"],
        "category": seed["category"],
        "other_category": other_category,
    }


def test_certification_filters(client, records):
    query = f"year=2024&academy_id={records['academy']}&category_id={records['category']}"
    assert len(client.get(f"/portfolio/certifications?{query}&limit=100").json()) == 1
    assert client.get("/portfolio/certifications?year=1990&limit=100").json() == []


def test_certification_nested_relations(client, records):
    body = client.get(f"/portfolio/certifications/{records['certification']}").json()
    assert body["academy"]["name"] == "Coursera"
    assert body["category"]["name"] == "Backend"
    assert body["validation_serial"] == "ABC-123"


def test_course_filters_by_category(client, records):
    assert len(client.get(f"/portfolio/courses?category_id={records['category']}").json()) == 1
    query = f"category_id={records['category']}&category_id={records['other_category']}"
    assert len(client.get(f"/portfolio/courses?{query}&limit=100").json()) == 2


def test_course_nested_relations(client, records):
    body = client.get(f"/portfolio/courses/{records['course']}").json()
    assert body["academy"]["name"] == "Coursera"
    assert body["category"]["name"] == "Backend"


@pytest.mark.parametrize("path", ["/portfolio/certifications", "/portfolio/courses"])
def test_missing_record_is_404(client, path):
    assert client.get(f"{path}/99999").status_code == 404


@pytest.mark.parametrize(
    ("path", "query"),
    [
        ("/portfolio/certifications", "year=1800"),
        # Courses expose no `year` filter, so use one they do have.
        ("/portfolio/courses", "category_id=99999"),
    ],
)
def test_empty_filter_result_is_an_empty_list(client, records, path, query):
    response = client.get(f"{path}?{query}")
    assert response.status_code == 200
    assert response.json() == []


def test_update_and_delete_course(client, auth, records):
    updated = client.patch(
        f"/portfolio/courses/{records['course']}", json={"year": 2019}, headers=auth
    )
    assert updated.status_code == 200
    assert updated.json()["year"] == 2019
    assert updated.json()["academy"]["name"] == "Coursera"

    assert client.delete(f"/portfolio/courses/{records['course']}", headers=auth).status_code == 204
    assert client.get(f"/portfolio/courses/{records['course']}").status_code == 404

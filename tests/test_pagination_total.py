"""The X-Total-Count header, and the certification is_main filter."""

import pytest

HEADER = "X-Total-Count"


def _total(response) -> int:
    assert HEADER in response.headers, f"{HEADER} missing from {response.url}"
    return int(response.headers[HEADER])


# ── X-Total-Count ────────────────────────────────────────────────────────────


def test_total_counts_all_rows_not_just_the_page(client, create, seed):
    for year in range(2018, 2025):  # 7 projects
        create(
            "/portfolio/projects",
            year=year,
            project_type_id=seed["project_type"],
            difficulty_level_id=seed["difficulty_level"],
        )

    response = client.get("/portfolio/projects?limit=2")
    assert len(response.json()) == 2
    assert _total(response) == 7


def test_total_respects_the_active_filters(client, create, seed):
    for is_main in (True, True, False):
        create(
            "/portfolio/projects",
            year=2025,
            is_main=is_main,
            project_type_id=seed["project_type"],
            difficulty_level_id=seed["difficulty_level"],
        )

    assert _total(client.get("/portfolio/projects")) == 3
    assert _total(client.get("/portfolio/projects?is_main=true")) == 2
    assert _total(client.get("/portfolio/projects?is_main=false")) == 1


def test_total_is_zero_for_an_empty_collection(client):
    response = client.get("/portfolio/projects")
    assert _total(response) == 0
    assert response.json() == []


def test_offset_does_not_change_the_total(client, create, seed):
    for year in range(2020, 2025):  # 5
        create(
            "/portfolio/projects",
            year=year,
            project_type_id=seed["project_type"],
            difficulty_level_id=seed["difficulty_level"],
        )

    page_two = client.get("/portfolio/projects?offset=3&limit=2")
    assert len(page_two.json()) == 2
    assert _total(page_two) == 5


@pytest.mark.parametrize(
    "path",
    [
        "/portfolio/projects",
        "/portfolio/courses",
        "/portfolio/certifications",
        "/portfolio/tags",
        "/portfolio/academies",
        "/portfolio/categories",
        "/portfolio/project-types",
        "/portfolio/difficulty-levels",
        "/portfolio/languages",
    ],
)
def test_every_list_endpoint_reports_a_total(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert HEADER in response.headers


def test_lookup_total_survives_pagination(client, create):
    for name in ("a", "b", "c", "d", "e"):
        create("/portfolio/tags", name=name)

    response = client.get("/portfolio/tags?limit=2")
    assert len(response.json()) == 2
    assert _total(response) == 5


def test_total_count_is_exposed_to_the_browser():
    """A cross-origin caller cannot read the header unless CORS advertises it."""
    from main import app

    cors = next(
        middleware for middleware in app.user_middleware if "CORSMiddleware" in str(middleware.cls)
    )
    assert HEADER in cors.kwargs["expose_headers"]


# ── Certification is_main ────────────────────────────────────────────────────


def test_certifications_default_to_not_main(create, seed):
    certification = create(
        "/portfolio/certifications",
        year=2025,
        academy_id=seed["academy"],
        category_id=seed["category"],
    )
    assert certification["is_main"] is False


def test_is_main_filter_selects_featured_certifications(client, create, seed):
    for is_main in (True, False, False):
        create(
            "/portfolio/certifications",
            year=2025,
            is_main=is_main,
            academy_id=seed["academy"],
            category_id=seed["category"],
        )

    featured = client.get("/portfolio/certifications?is_main=true")
    assert featured.status_code == 200
    assert len(featured.json()) == 1
    assert featured.json()[0]["is_main"] is True

    assert len(client.get("/portfolio/certifications?is_main=false").json()) == 2
    assert len(client.get("/portfolio/certifications").json()) == 3


def test_is_main_is_patchable(client, auth, create, seed):
    certification = create(
        "/portfolio/certifications",
        year=2025,
        academy_id=seed["academy"],
        category_id=seed["category"],
    )
    response = client.patch(
        f"/portfolio/certifications/{certification['id']}",
        json={"is_main": True},
        headers=auth,
    )
    assert response.status_code == 200
    assert response.json()["is_main"] is True

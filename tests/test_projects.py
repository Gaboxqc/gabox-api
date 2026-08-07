"""Project filtering, search and eager loading."""

import pytest

from api.portfolio.models import ProjectTag


@pytest.fixture(name="catalogue")
def catalogue_fixture(client, create, auth, seed, engine):
    """Three projects with distinct types, levels and tags."""
    from sqlmodel import Session

    cli = create("/portfolio/project-types", name="CLI")["id"]
    hard = create("/portfolio/difficulty-levels", name="Hard")["id"]
    python = create("/portfolio/tags", name="Python")["id"]
    javascript = create("/portfolio/tags", name="JS")["id"]

    rows = [
        (seed["project_type"], seed["difficulty_level"], True),
        (seed["project_type"], hard, False),
        (cli, seed["difficulty_level"], False),
    ]
    ids = []
    for index, (type_id, level_id, is_main) in enumerate(rows):
        project = create(
            "/portfolio/projects",
            year=2020 + index,
            project_type_id=type_id,
            difficulty_level_id=level_id,
            is_main=is_main,
        )
        ids.append(project["id"])
        client.post(
            f"/portfolio/projects/{project['id']}/translations",
            json={
                "language_code": "en",
                "title": f"Project {index} 100% done",
                "description": "a long enough description",
            },
            headers=auth,
        )

    # Both tags on the first project, so an EXISTS filter matching either one
    # still returns a single row.
    with Session(engine) as session:
        session.add(ProjectTag(project_id=ids[0], tag_id=python))
        session.add(ProjectTag(project_id=ids[0], tag_id=javascript))
        session.commit()

    return {
        "ids": ids,
        "web": seed["project_type"],
        "cli": cli,
        "easy": seed["difficulty_level"],
        "hard": hard,
        "python": python,
        "js": javascript,
    }


def test_lists_every_project(client, catalogue):
    assert len(client.get("/portfolio/projects?limit=100").json()) == 3


def test_filters_by_is_main(client, catalogue):
    assert len(client.get("/portfolio/projects?is_main=true&limit=100").json()) == 1


def test_filters_by_project_type(client, catalogue):
    web = catalogue["web"]
    assert len(client.get(f"/portfolio/projects?project_type_id={web}&limit=100").json()) == 2


def test_accepts_repeated_filter_values(client, catalogue):
    query = f"project_type_id={catalogue['web']}&project_type_id={catalogue['cli']}"
    assert len(client.get(f"/portfolio/projects?{query}&limit=100").json()) == 3


def test_filters_combine_as_and(client, catalogue):
    query = f"project_type_id={catalogue['web']}&difficulty_level_id={catalogue['hard']}"
    assert len(client.get(f"/portfolio/projects?{query}&limit=100").json()) == 1


def test_tag_filter_does_not_duplicate_rows(client, catalogue):
    """One project carrying both matched tags must appear once, not twice."""
    query = f"tag_id={catalogue['python']}&tag_id={catalogue['js']}"
    body = client.get(f"/portfolio/projects?{query}&limit=100").json()
    assert len(body) == 1
    assert body[0]["id"] == catalogue["ids"][0]


def test_search_matches_translated_title(client, catalogue):
    assert len(client.get("/portfolio/projects?search=Project 1&limit=100").json()) == 1


def test_search_with_no_match_returns_empty_list(client, catalogue):
    response = client.get("/portfolio/projects?search=nonexistent&limit=100")
    assert response.status_code == 200
    assert response.json() == []


def test_search_treats_underscore_literally(client, catalogue):
    """`_` is LIKE's single-character wildcard.

    Unescaped, "Project_0" would match the stored title "Project 0 100% done".
    """
    assert client.get("/portfolio/projects?search=Project_0&limit=100").json() == []
    assert len(client.get("/portfolio/projects?search=Project 0&limit=100").json()) == 1


def test_search_treats_percent_literally(client, catalogue):
    assert len(client.get("/portfolio/projects?search=100%25 done&limit=100").json()) == 3
    assert client.get("/portfolio/projects?search=100%25zzz&limit=100").json() == []


def test_pagination_does_not_repeat_or_skip(client, catalogue):
    first = client.get("/portfolio/projects?limit=2").json()
    second = client.get("/portfolio/projects?limit=2&offset=2").json()
    assert len(first) == 2
    assert len(second) == 1
    assert not ({p["id"] for p in first} & {p["id"] for p in second})


def test_detail_includes_nested_relations(client, catalogue):
    body = client.get(f"/portfolio/projects/{catalogue['ids'][0]}").json()
    assert body["project_type"]["name"] == "Web"
    assert body["difficulty_level"]["name"] == "Easy"
    assert len(body["tags"]) == 2
    assert len(body["translations"]) == 1


def test_eager_loading_avoids_n_plus_one(client, catalogue, queries):
    """Query count must not grow with the number of rows returned."""

    def count_for(limit: int) -> int:
        queries.clear()
        client.get(f"/portfolio/projects?limit={limit}")
        return len([q for q in queries if q.lstrip().upper().startswith("SELECT")])

    assert count_for(100) == count_for(1)


def test_update_response_keeps_nested_relations(client, auth, catalogue):
    response = client.patch(
        f"/portfolio/projects/{catalogue['ids'][0]}", json={"year": 1999}, headers=auth
    )
    assert response.status_code == 200
    assert response.json()["year"] == 1999
    assert len(response.json()["tags"]) == 2


def test_delete_removes_the_project(client, auth, catalogue):
    project_id = catalogue["ids"][2]
    assert client.delete(f"/portfolio/projects/{project_id}", headers=auth).status_code == 204
    assert client.get(f"/portfolio/projects/{project_id}").status_code == 404


def test_missing_project_is_404(client):
    response = client.get("/portfolio/projects/99999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Project with id 99999 not found"

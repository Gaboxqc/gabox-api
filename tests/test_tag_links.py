"""Attaching tags to projects, courses and certifications.

The link tables and the `tags` field on every read schema already existed, but no
write schema accepted tag ids, so the links were unreachable through the API.
"""

import pytest


@pytest.fixture(name="tags")
def tags_fixture(create) -> dict[str, int]:
    return {
        "react": create("/portfolio/tags", name="React")["id"],
        "python": create("/portfolio/tags", name="Python")["id"],
        "rust": create("/portfolio/tags", name="Rust")["id"],
    }


def _names(payload) -> set[str]:
    return {tag["name"] for tag in payload["tags"]}


# ── Projects ─────────────────────────────────────────────────────────────────


def test_create_project_with_tags(create, seed, tags):
    project = create(
        "/portfolio/projects",
        year=2025,
        project_type_id=seed["project_type"],
        difficulty_level_id=seed["difficulty_level"],
        tag_ids=[tags["react"], tags["python"]],
    )
    assert _names(project) == {"React", "Python"}


def test_create_project_without_tags_is_still_valid(create, seed):
    project = create(
        "/portfolio/projects",
        year=2025,
        project_type_id=seed["project_type"],
        difficulty_level_id=seed["difficulty_level"],
    )
    assert project["tags"] == []


def test_patch_replaces_the_whole_tag_set(client, auth, create, seed, tags):
    project = create(
        "/portfolio/projects",
        year=2025,
        project_type_id=seed["project_type"],
        difficulty_level_id=seed["difficulty_level"],
        tag_ids=[tags["react"], tags["python"]],
    )
    response = client.patch(
        f"/portfolio/projects/{project['id']}",
        json={"tag_ids": [tags["rust"]]},
        headers=auth,
    )
    assert response.status_code == 200
    assert _names(response.json()) == {"Rust"}


def test_patch_with_empty_list_removes_every_tag(client, auth, create, seed, tags):
    project = create(
        "/portfolio/projects",
        year=2025,
        project_type_id=seed["project_type"],
        difficulty_level_id=seed["difficulty_level"],
        tag_ids=[tags["react"]],
    )
    response = client.patch(
        f"/portfolio/projects/{project['id']}", json={"tag_ids": []}, headers=auth
    )
    assert response.status_code == 200
    assert response.json()["tags"] == []


def test_patch_without_tag_ids_leaves_tags_alone(client, auth, create, seed, tags):
    """`None` means "not mentioned", which must not be read as "clear them"."""
    project = create(
        "/portfolio/projects",
        year=2025,
        project_type_id=seed["project_type"],
        difficulty_level_id=seed["difficulty_level"],
        tag_ids=[tags["react"]],
    )
    response = client.patch(
        f"/portfolio/projects/{project['id']}", json={"year": 2026}, headers=auth
    )
    assert response.status_code == 200
    assert response.json()["year"] == 2026
    assert _names(response.json()) == {"React"}


def test_unknown_tag_id_is_rejected(client, auth, seed):
    response = client.post(
        "/portfolio/projects",
        json={
            "year": 2025,
            "project_type_id": seed["project_type"],
            "difficulty_level_id": seed["difficulty_level"],
            "tag_ids": [9999],
        },
        headers=auth,
    )
    assert response.status_code == 422
    assert "9999" in response.json()["detail"]


def test_duplicate_tag_ids_are_collapsed(create, seed, tags):
    """The link table's composite key would otherwise fail on the second copy."""
    project = create(
        "/portfolio/projects",
        year=2025,
        project_type_id=seed["project_type"],
        difficulty_level_id=seed["difficulty_level"],
        tag_ids=[tags["react"], tags["react"], tags["python"]],
    )
    assert len(project["tags"]) == 2


def test_tagging_requires_authentication(client, seed, tags):
    response = client.post(
        "/portfolio/projects",
        json={
            "year": 2025,
            "project_type_id": seed["project_type"],
            "difficulty_level_id": seed["difficulty_level"],
            "tag_ids": [tags["react"]],
        },
    )
    assert response.status_code == 401


# ── Courses and certifications ───────────────────────────────────────────────


def test_create_course_with_tags(create, seed, tags):
    course = create(
        "/portfolio/courses",
        year=2025,
        academy_id=seed["academy"],
        category_id=seed["category"],
        tag_ids=[tags["python"]],
    )
    assert _names(course) == {"Python"}


def test_create_certification_with_tags(create, seed, tags):
    certification = create(
        "/portfolio/certifications",
        year=2025,
        academy_id=seed["academy"],
        category_id=seed["category"],
        tag_ids=[tags["rust"], tags["react"]],
    )
    assert _names(certification) == {"Rust", "React"}


def test_patch_course_tags(client, auth, create, seed, tags):
    course = create(
        "/portfolio/courses",
        year=2025,
        academy_id=seed["academy"],
        category_id=seed["category"],
        tag_ids=[tags["python"]],
    )
    response = client.patch(
        f"/portfolio/courses/{course['id']}", json={"tag_ids": [tags["rust"]]}, headers=auth
    )
    assert _names(response.json()) == {"Rust"}


def test_patch_certification_tags(client, auth, create, seed, tags):
    certification = create(
        "/portfolio/certifications",
        year=2025,
        academy_id=seed["academy"],
        category_id=seed["category"],
    )
    response = client.patch(
        f"/portfolio/certifications/{certification['id']}",
        json={"tag_ids": [tags["react"]]},
        headers=auth,
    )
    assert _names(response.json()) == {"React"}


def test_tags_are_filterable_after_being_attached(client, create, seed, tags):
    """The tag_id filter already existed; now there is a way to populate it."""
    create(
        "/portfolio/projects",
        year=2025,
        project_type_id=seed["project_type"],
        difficulty_level_id=seed["difficulty_level"],
        tag_ids=[tags["rust"]],
    )
    create(
        "/portfolio/projects",
        year=2024,
        project_type_id=seed["project_type"],
        difficulty_level_id=seed["difficulty_level"],
        tag_ids=[tags["react"]],
    )

    response = client.get(f"/portfolio/projects?tag_id={tags['rust']}")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert _names(response.json()[0]) == {"Rust"}


def test_deleting_a_tag_removes_the_link_not_the_project(client, auth, create, seed, tags):
    project = create(
        "/portfolio/projects",
        year=2025,
        project_type_id=seed["project_type"],
        difficulty_level_id=seed["difficulty_level"],
        tag_ids=[tags["rust"]],
    )
    assert client.delete(f"/portfolio/tags/{tags['rust']}", headers=auth).status_code == 204

    remaining = client.get(f"/portfolio/projects/{project['id']}")
    assert remaining.status_code == 200
    assert remaining.json()["tags"] == []

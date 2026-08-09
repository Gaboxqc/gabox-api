"""Academy logos.

The logo lives on the academy so that changing it is one edit rather than one per
certificate, which means it has to travel with every nested `academy` payload.
"""

LOGO = "https://assets.gabrielmayorga.dev/academies/platzi.png"


def test_academy_can_be_created_with_a_logo(create):
    academy = create("/portfolio/academies", name="Platzi", image_url=LOGO)
    assert academy["image_url"] == LOGO


def test_logo_is_optional(create):
    assert create("/portfolio/academies", name="Coursera")["image_url"] is None


def test_logo_can_be_added_later(client, auth, create):
    academy = create("/portfolio/academies", name="edX")
    response = client.patch(
        f"/portfolio/academies/{academy['id']}", json={"image_url": LOGO}, headers=auth
    )
    assert response.status_code == 200
    assert response.json()["image_url"] == LOGO


def test_logo_can_be_cleared(client, auth, create):
    academy = create("/portfolio/academies", name="Udemy", image_url=LOGO)
    response = client.patch(
        f"/portfolio/academies/{academy['id']}", json={"image_url": None}, headers=auth
    )
    assert response.status_code == 200
    assert response.json()["image_url"] is None


def test_renaming_does_not_drop_the_logo(client, auth, create):
    """PATCH is partial, so a name-only edit must leave the logo alone."""
    academy = create("/portfolio/academies", name="Platsi", image_url=LOGO)
    response = client.patch(
        f"/portfolio/academies/{academy['id']}", json={"name": "Platzi"}, headers=auth
    )
    assert response.json() == {"id": academy["id"], "name": "Platzi", "image_url": LOGO}


def test_certification_payload_carries_the_academy_logo(client, create, seed):
    """The public cards read the logo from the nested academy, so it has to be
    present without a second request."""
    academy = create("/portfolio/academies", name="Platzi", image_url=LOGO)
    create(
        "/portfolio/certifications",
        year=2025,
        academy_id=academy["id"],
        category_id=seed["category"],
    )

    listed = client.get("/portfolio/certifications").json()
    assert listed[0]["academy"]["image_url"] == LOGO


def test_course_payload_carries_the_academy_logo(client, create, seed):
    academy = create("/portfolio/academies", name="Platzi", image_url=LOGO)
    create("/portfolio/courses", year=2025, academy_id=academy["id"], category_id=seed["category"])

    listed = client.get("/portfolio/courses").json()
    assert listed[0]["academy"]["image_url"] == LOGO


def test_logo_requires_authentication(client):
    response = client.post("/portfolio/academies", json={"name": "Nope", "image_url": LOGO})
    assert response.status_code == 401

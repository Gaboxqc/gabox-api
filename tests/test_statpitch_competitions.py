"""The competition registry.

`competition_id` used to be a bare string with nothing behind it — the API
returned "ENG.PL" and no readable name at all. These tests are about that string
now meaning something, and meaning it in one place.
"""

from sqlmodel import Session, select

from api.statpitch.competitions import StatPitchCompetition, all_competitions, by_id, seed
from api.statpitch.leagues import ALL_COMPETITIONS, STATPITCH_ODDS_COVERAGE

# ── Seeding ──────────────────────────────────────────────────────────────────


def test_all_twelve_are_seeded(engine):
    with Session(engine) as db:
        assert seed(db) == 12
        assert {row.competition_id for row in all_competitions(db)} == ALL_COMPETITIONS


def test_seeding_twice_adds_nothing(engine):
    """Safe to call on every boot, which is the point."""
    with Session(engine) as db:
        seed(db)
        assert seed(db) == 0
        assert len(all_competitions(db)) == 12


def test_seeding_leaves_an_existing_icon_alone(engine):
    """A re-seed must not undo a backfill."""
    with Session(engine) as db:
        seed(db)
        row = by_id(db, "ENG.PL")
        row.icon_url = "https://assets.example.com/statpitch/competitions/eng-pl/abc-512.webp"
        db.add(row)
        db.commit()

        seed(db)
        assert by_id(db, "ENG.PL").icon_url is not None


def test_every_competition_has_a_name_and_a_short_name(engine):
    with Session(engine) as db:
        seed(db)
        for row in all_competitions(db):
            assert row.name
            assert row.short_name
            assert row.espn_slug


def test_the_short_name_is_the_one_that_fits_a_chip(engine):
    with Session(engine) as db:
        seed(db)
        assert by_id(db, "ENG.PL").name == "English Premier League"
        assert by_id(db, "ENG.PL").short_name == "Premier League"


def test_an_unknown_competition_is_not_invented(engine):
    with Session(engine) as db:
        seed(db)
        assert by_id(db, "MARS.SUPERLEAGUE") is None


# ── The endpoint ─────────────────────────────────────────────────────────────


def test_the_endpoint_lists_every_competition(client, engine):
    with Session(engine) as db:
        seed(db)

    body = client.get("/statpitch/competitions").json()
    assert len(body) == 12
    assert {row["competition_id"] for row in body} == ALL_COMPETITIONS


def test_the_endpoint_is_ungated(client, engine):
    """Which competitions exist is navigation, not product — an anonymous
    visitor needs it to render a filter bar at all."""
    with Session(engine) as db:
        seed(db)

    assert client.get("/statpitch/competitions").status_code == 200


def test_the_endpoint_says_which_are_free(client, engine):
    """So the seven cups can be shown as an upgrade rather than discovered by
    getting an empty list back."""
    with Session(engine) as db:
        seed(db)

    body = client.get("/statpitch/competitions").json()
    free = {row["competition_id"] for row in body if row["free_tier"]}

    assert free == STATPITCH_ODDS_COVERAGE
    assert len(free) == 5


def test_an_unseeded_registry_returns_an_empty_list(client):
    """Not a 500. An empty registry is a deploy that has not seeded yet."""
    response = client.get("/statpitch/competitions")
    assert response.status_code == 200
    assert response.json() == []


# ── On a fixture ─────────────────────────────────────────────────────────────


def test_a_fixture_reports_its_competition_name(client, auth, make_fixture, seed_fixtures):
    """The frontend used to get "ENG.PL" and nothing else."""
    seed_fixtures(make_fixture(competition_id="ENG.PL"))

    (body,) = client.get("/statpitch/fixtures/today", headers=auth).json()
    assert body["competition_id"] == "ENG.PL"
    assert body["competition_name"] == "English Premier League"
    assert body["competition_short_name"] == "Premier League"


def test_the_name_reaches_the_free_shape_too(client, make_fixture, seed_fixtures):
    """A competition heading is not something anybody is paying for."""
    seed_fixtures(make_fixture(competition_id="ENG.PL"))

    (body,) = client.get("/statpitch/fixtures/today").json()
    assert body["competition_name"] == "English Premier League"


def test_an_icon_reaches_a_cached_fixture_immediately(
    client, auth, engine, make_fixture, seed_fixtures
):
    """Read through the reference, so an icon resolved after a fixture was
    cached needs no sync to appear — the same property the crests have."""
    seed_fixtures(make_fixture(competition_id="ENG.PL"))

    (before,) = client.get("/statpitch/fixtures/today", headers=auth).json()
    assert before["competition_icon_url"] is None

    icon = "https://assets.example.com/statpitch/competitions/eng-pl/abc-512.webp"
    with Session(engine) as db:
        row = by_id(db, "ENG.PL")
        row.icon_url = icon
        db.add(row)
        db.commit()

    # No re-sync, no second pass over the fixture cache.
    (after,) = client.get("/statpitch/fixtures/today", headers=auth).json()
    assert after["competition_icon_url"] == icon


def test_the_competition_costs_no_extra_query(engine, make_fixture, seed_fixtures):
    """Joined with the clubs, so a fixture list stays one statement."""
    from sqlalchemy import event

    from api.statpitch.models import StatPitchFixture

    seed_fixtures(*[make_fixture() for _ in range(5)])

    count = {"n": 0}
    event.listen(
        engine, "before_cursor_execute", lambda *a, **k: count.__setitem__("n", count["n"] + 1)
    )

    with Session(engine) as db:
        rows = db.exec(select(StatPitchFixture)).all()
        _ = [(row.competition_name, row.home_team, row.away_team) for row in rows]

    assert len(rows) == 5
    assert count["n"] == 1


def test_a_fixture_cannot_name_a_competition_that_does_not_exist(engine, make_fixture):
    """The foreign key is what stops "ENG.PLL" becoming a silent thirteenth
    competition nobody notices."""
    from sqlalchemy.exc import IntegrityError

    with Session(engine) as db:
        seed(db)
        row = db.exec(select(StatPitchCompetition)).first()
        assert row is not None

        fixture = make_fixture()
        fixture.competition_id = "ENG.PLL"
        db.add(fixture)
        try:
            db.commit()
        except IntegrityError:
            return
    raise AssertionError("an unknown competition_id was accepted")

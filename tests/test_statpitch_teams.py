"""The club registry, and how the sync attaches crests through it."""

from datetime import date

import pytest
from sqlmodel import Session, select

from api.statpitch.models import StatPitchFixture
from api.statpitch.teams import (
    StatPitchTeam,
    link_fixtures,
    resolve_team,
    slug_for,
    teams_by_slug,
)


def _fixture(competition_id: str = "ENG.PL") -> StatPitchFixture:
    """A fixture with only the fields the registry looks at.

    No club names: those are what `link_fixtures` is being asked to attach.
    """
    return StatPitchFixture(
        fixture_id=f"f-{competition_id}-{id(object())}",
        competition_id=competition_id,
        match_date=date(2026, 8, 20),
        source_date=date(2026, 8, 20),
        model_version="test",
        home_xg=1.4,
        away_xg=1.1,
        home_win_prob=0.45,
        draw_prob=0.27,
        away_win_prob=0.28,
        over_1_5=0.78,
        over_2_5=0.52,
        over_3_5=0.27,
        btts_yes=0.54,
        btts_no=0.46,
    )


# ── Slugging ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Arsenal", "  arsenal fc  "),
        ("Club Atletico de Madrid", "Atletico Madrid"),
        ("Manchester United", "Man United"),
        ("Wolverhampton Wanderers", "Wolves"),
    ],
)
def test_spelling_variants_land_on_one_slug(left, right):
    assert slug_for(left) == slug_for(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Bayern Munich", "FC Bayern Munchen"),
        ("Sporting", "Sporting Gijon"),
    ],
)
def test_cross_source_spellings_are_not_folded(left, right):
    """Pinning the limit, not a wish. The key is exact; joining another
    source's names onto these rows is `matching.similarity`'s job, and the crest
    resolver is where that happens.

    Individual pairs can be folded deliberately by adding an alias — Espanyol
    was moved out of this list that way — but nothing folds them automatically."""
    assert slug_for(left) != slug_for(right)


def test_a_name_made_entirely_of_noise_still_gets_its_own_slug():
    """Normalisation strips corporate-form tokens; if that leaves nothing, the
    raw name has to stand in, or every such club collapses into one row."""
    assert slug_for("FC") != slug_for("SC")
    assert slug_for("FC") != ""


def test_different_clubs_keep_different_slugs():
    assert slug_for("Real Madrid") != slug_for("Real Betis")


# ── Registration ─────────────────────────────────────────────────────────────


def test_an_unseen_club_is_recorded(engine):
    with Session(engine) as db:
        team = resolve_team(db, "Arsenal", "ENG.PL")

        assert team.id is not None
        assert team.slug == "arsenal"
        assert team.source_name == "Arsenal"
        assert team.competition_id == "ENG.PL"
        assert team.crest_url is None


def test_registering_the_same_club_twice_returns_the_same_row(engine):
    with Session(engine) as db:
        first = resolve_team(db, "Arsenal", "ENG.PL")
        second = resolve_team(db, "  arsenal fc ", "ENG.FA_CUP")

        assert first.id == second.id
        # The first sighting wins: a club met in the league and again in the cup
        # is one club, not two, and its recorded competition does not flap.
        assert second.competition_id == "ENG.PL"
        assert len(db.exec(select(StatPitchTeam)).all()) == 1


def test_lookup_by_slug_returns_only_what_was_asked_for(engine):
    with Session(engine) as db:
        resolve_team(db, "Arsenal", "ENG.PL")
        resolve_team(db, "Chelsea", "ENG.PL")

        found = teams_by_slug(db, {"arsenal", "nobody"})

    assert set(found) == {"arsenal"}


def test_looking_up_nothing_asks_the_database_nothing(engine):
    with Session(engine) as db:
        assert teams_by_slug(db, set()) == {}


# ── Linking fixtures ─────────────────────────────────────────────────────────


def test_linking_registers_both_sides(engine):
    with Session(engine) as db:
        fixture = _fixture()
        clubs, missing = link_fixtures(db, [(fixture, "Arsenal", "Chelsea")])

        assert clubs == 2
        # Two sides, neither with a crest yet.
        assert missing == 2
        assert {row.slug for row in db.exec(select(StatPitchTeam)).all()} == {"arsenal", "chelsea"}
        assert fixture.home_team_id is not None
        assert fixture.away_team_id is not None


def test_a_club_appearing_twice_is_registered_once(engine):
    with Session(engine) as db:
        clubs, _ = link_fixtures(
            db,
            [
                (_fixture(), "Arsenal", "Chelsea"),
                (_fixture(), "Arsenal", "Everton"),
            ],
        )

        assert clubs == 3
        assert len(db.exec(select(StatPitchTeam)).all()) == 3


def test_a_fixture_reads_its_clubs_through_the_reference(engine):
    """The names are not copied onto the row any more — they are read back."""
    with Session(engine) as db:
        fixture = _fixture()
        link_fixtures(db, [(fixture, "Arsenal", "Chelsea")])
        db.add(fixture)
        db.commit()
        db.refresh(fixture)

        assert fixture.home_team == "Arsenal"
        assert fixture.away_team == "Chelsea"


def test_a_crest_reaches_a_fixture_immediately(engine):
    """The point of the foreign key. The crest used to be snapshotted onto the
    row, so one resolved after a fixture was cached stayed invisible to it until
    the next sync overwrote the column."""
    with Session(engine) as db:
        fixture = _fixture()
        link_fixtures(db, [(fixture, "Arsenal", "Chelsea")])
        db.add(fixture)
        db.commit()

        assert fixture.home_crest_url is None

        team = db.exec(select(StatPitchTeam).where(StatPitchTeam.slug == "arsenal")).one()
        team.crest_url = "https://cdn.example.com/statpitch/crests/arsenal/abc123-512.webp"
        db.add(team)
        db.commit()

        # No re-sync, no second pass, no refresh of the fixture.
        assert fixture.home_crest_url == team.crest_url
        assert fixture.away_crest_url is None


def test_linking_nothing_is_harmless(engine):
    with Session(engine) as db:
        assert link_fixtures(db, []) == (0, 0)


def test_the_registry_survives_a_fixture_being_pruned(engine):
    """Fixtures live three days; the registry is permanent. Losing the crest
    with the cache is exactly what this table exists to prevent."""
    with Session(engine) as db:
        fixture = _fixture()
        link_fixtures(db, [(fixture, "Arsenal", "Chelsea")])
        db.add(fixture)
        db.commit()

        db.delete(fixture)
        db.commit()

        assert len(db.exec(select(StatPitchTeam)).all()) == 2

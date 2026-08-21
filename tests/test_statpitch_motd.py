"""The Match of the Day pick.

The property that matters is stability: once the day's pick is made it must not
move, however many times the sync runs or how far the prices drift. Most of what
follows is that one claim, approached from different angles.
"""

import pytest
from sqlmodel import Session, select

from api.statpitch.clock import today_local
from api.statpitch.motd import StatPitchMatchOfTheDay, choose, ensure, fixture_for, stored_for


@pytest.fixture(name="pick_from")
def pick_from_fixture(engine, seed_fixtures):
    """Seed fixtures, then make the day's pick from them."""

    def _pick_from(*fixtures):
        stored = seed_fixtures(*fixtures)
        with Session(engine) as db:
            rows = list(db.exec(select(type(stored[0]))).all())
            return ensure(db, today_local(), rows)

    return _pick_from


# ── Choosing ─────────────────────────────────────────────────────────────────


def test_the_clearest_call_wins(make_fixture):
    weak = make_fixture(home_win_prob=0.40, draw_prob=0.30, away_win_prob=0.30)
    strong = make_fixture(home_win_prob=0.78, draw_prob=0.12, away_win_prob=0.10)

    assert choose([weak, strong]) is strong


def test_an_away_favourite_counts_too(make_fixture):
    """The pick is "somebody will clearly win", not "the home side will"."""
    home = make_fixture(home_win_prob=0.55, draw_prob=0.25, away_win_prob=0.20)
    away = make_fixture(home_win_prob=0.15, draw_prob=0.20, away_win_prob=0.65)

    assert choose([home, away]) is away


def test_a_confident_draw_is_not_a_pick(make_fixture):
    """ "We are confident this is a draw" is not a match anyone wants
    recommended."""
    drawish = make_fixture(home_win_prob=0.20, draw_prob=0.60, away_win_prob=0.20)
    decisive = make_fixture(home_win_prob=0.50, draw_prob=0.25, away_win_prob=0.25)

    assert choose([drawish, decisive]) is decisive


def test_a_confirmed_kickoff_is_preferred_over_a_stronger_placeholder(make_fixture):
    """Roughly 88% of fixtures sit on a matchday placeholder. Billing one as
    today's match is a claim the schedule does not support."""
    placeholder = make_fixture(
        home_win_prob=0.90, draw_prob=0.05, away_win_prob=0.05, date_confirmed=False
    )
    confirmed = make_fixture(
        home_win_prob=0.60, draw_prob=0.20, away_win_prob=0.20, date_confirmed=True
    )

    assert choose([placeholder, confirmed]) is confirmed


def test_a_day_with_nothing_confirmed_still_gets_a_pick(make_fixture):
    weak = make_fixture(home_win_prob=0.40, date_confirmed=False)
    strong = make_fixture(home_win_prob=0.80, date_confirmed=False)

    assert choose([weak, strong]) is strong


def test_the_pick_comes_from_what_free_accounts_can_see(make_fixture):
    """It is on all three columns of the pricing page, so picking a Champions
    League tie would name a match free accounts are never shown."""
    cup = make_fixture(competition_id="UEFA.UCL", home_win_prob=0.95, draw_prob=0.03)
    league = make_fixture(competition_id="ENG.PL", home_win_prob=0.55, draw_prob=0.25)

    assert choose([cup, league]) is league


def test_a_day_of_only_cup_ties_has_no_pick(make_fixture):
    assert choose([make_fixture(competition_id="UEFA.UCL")]) is None


def test_nothing_to_pick_from_is_not_an_error():
    assert choose([]) is None


# ── Stability ────────────────────────────────────────────────────────────────


def test_the_pick_is_written_down(engine, pick_from, make_fixture):
    pick_from(make_fixture(home_team="Arsenal", home_win_prob=0.80, draw_prob=0.12))

    with Session(engine) as db:
        stored = stored_for(db, today_local())

    assert stored is not None
    assert stored.home_team == "Arsenal"
    assert stored.win_probability == pytest.approx(0.80)


def test_a_second_sync_does_not_move_it(engine, seed_fixtures, make_fixture):
    """The whole point. A price moving must not change what the day's match is."""
    from api.statpitch.models import StatPitchFixture

    seed_fixtures(
        make_fixture(home_team="First", home_win_prob=0.70, draw_prob=0.15, away_win_prob=0.15),
    )
    with Session(engine) as db:
        rows = list(db.exec(select(StatPitchFixture)).all())
        first = ensure(db, today_local(), rows)
        assert first.home_team == "First"

    # A stronger fixture appears on the next sync.
    seed_fixtures(
        make_fixture(home_team="Later", home_win_prob=0.95, draw_prob=0.03, away_win_prob=0.02),
    )
    with Session(engine) as db:
        rows = list(db.exec(select(StatPitchFixture)).all())
        second = ensure(db, today_local(), rows)

    assert second.home_team == "First"


def test_running_the_sync_repeatedly_writes_one_row(engine, seed_fixtures, make_fixture):
    from api.statpitch.models import StatPitchFixture

    seed_fixtures(make_fixture(home_win_prob=0.70, draw_prob=0.15))
    with Session(engine) as db:
        rows = list(db.exec(select(StatPitchFixture)).all())
        for _ in range(5):
            ensure(db, today_local(), rows)

        assert len(db.exec(select(StatPitchMatchOfTheDay)).all()) == 1


def test_a_vanished_pick_is_replaced(engine, seed_fixtures, make_fixture):
    """A postponement should not leave the day pointing at a match nobody can
    look at."""
    from api.statpitch.models import StatPitchFixture

    seed_fixtures(make_fixture(home_team="Postponed", home_win_prob=0.80, draw_prob=0.12))
    with Session(engine) as db:
        rows = list(db.exec(select(StatPitchFixture)).all())
        ensure(db, today_local(), rows)

        for row in rows:
            db.delete(row)
        db.commit()

    seed_fixtures(make_fixture(home_team="Replacement", home_win_prob=0.60, draw_prob=0.20))
    with Session(engine) as db:
        rows = list(db.exec(select(StatPitchFixture)).all())
        replacement = ensure(db, today_local(), rows)

    assert replacement.home_team == "Replacement"


def test_the_record_outlives_the_fixture(engine, seed_fixtures, make_fixture):
    """Fixtures live three days; the decision is worth keeping longer."""
    from api.statpitch.models import StatPitchFixture

    seed_fixtures(make_fixture(home_team="Arsenal", home_win_prob=0.80, draw_prob=0.12))
    with Session(engine) as db:
        rows = list(db.exec(select(StatPitchFixture)).all())
        ensure(db, today_local(), rows)

        for row in rows:
            db.delete(row)
        db.commit()

        stored = stored_for(db, today_local())
        assert stored.home_team == "Arsenal"
        # But there is no fixture left to serve.
        assert fixture_for(db, today_local()) is None


def test_a_day_with_no_fixtures_records_nothing(engine):
    with Session(engine) as db:
        assert ensure(db, today_local(), []) is None
        assert stored_for(db, today_local()) is None


# ── Over the wire ────────────────────────────────────────────────────────────


def test_the_endpoint_serves_the_stored_pick(client, engine, seed_fixtures, make_fixture):
    from api.statpitch.models import StatPitchFixture

    seed_fixtures(
        make_fixture(home_team="Chosen", home_win_prob=0.70, draw_prob=0.15, away_win_prob=0.15),
        make_fixture(home_team="Stronger", home_win_prob=0.95, draw_prob=0.03, away_win_prob=0.02),
    )
    with Session(engine) as db:
        # Pick the weaker one deliberately, so serving the stored decision is
        # distinguishable from recomputing.
        rows = [row for row in db.exec(select(StatPitchFixture)).all() if row.home_team == "Chosen"]
        ensure(db, today_local(), rows)

    body = client.get("/statpitch/fixtures/today/best").json()
    assert body["home_team"] == "Chosen"


def test_the_endpoint_falls_back_before_the_first_sync(client, seed_fixtures, make_fixture):
    """A day whose sync has not run yet still answers, rather than 404ing."""
    seed_fixtures(make_fixture(home_team="Best", home_win_prob=0.80, draw_prob=0.12))

    response = client.get("/statpitch/fixtures/today/best")
    assert response.status_code == 200
    assert response.json()["home_team"] == "Best"


def test_the_fallback_does_not_write(client, engine, seed_fixtures, make_fixture):
    """A read request is the wrong place to decide something the whole product
    then has to agree on."""
    seed_fixtures(make_fixture(home_win_prob=0.80, draw_prob=0.12))
    client.get("/statpitch/fixtures/today/best")

    with Session(engine) as db:
        assert db.exec(select(StatPitchMatchOfTheDay)).all() == []


def test_an_empty_day_is_still_a_404(client):
    assert client.get("/statpitch/fixtures/today/best").status_code == 404


def test_free_and_paid_are_shown_the_same_match(client, engine, seed_fixtures, make_fixture):
    """One pick, for everybody — otherwise there is no such thing as *the*
    match of the day."""
    from api.statpitch.models import StatPitchFixture

    seed_fixtures(make_fixture(home_team="Everyone", home_win_prob=0.75, draw_prob=0.15))
    with Session(engine) as db:
        rows = list(db.exec(select(StatPitchFixture)).all())
        ensure(db, today_local(), rows)

    anonymous = client.get("/statpitch/fixtures/today/best").json()
    with_key = client.get(
        "/statpitch/fixtures/today/best", headers={"X-API-KEY": "test-master-key"}
    ).json()

    assert anonymous["home_team"] == with_key["home_team"] == "Everyone"
    # Same match, different depth.
    assert anonymous["locked"] is False
    assert "odds_home" not in anonymous
    assert "odds_home" in with_key

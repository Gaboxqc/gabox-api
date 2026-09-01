"""The daily pick endpoint, and the caveat that has to travel with it.

Most of this file is about one rule: a recommendation is never served without
the statement qualifying it. The rule behind these picks carries five seasons of
measured closing-line value but runs on a price panel that has none, so a pick
rendered bare claims more than the evidence supports.
"""

from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from api.statpitch.bets import build_bets_today, caveat_for
from api.statpitch.clock import today_local
from api.statpitch.models import StatPitchBetDay, StatPitchSelection

UPSTREAM_CAVEAT = (
    "selection_rule.status=experimental. The rule has five seasons of measured CLV; "
    "the 25-book panel it now runs on has none."
)


@pytest.fixture(name="staked")
def staked_fixture(engine, make_fixture, seed_fixtures):
    """A fixture today carrying one staked StatPitch selection."""

    def _make(*, day=None, **overrides):
        day = day or today_local()
        fixture = seed_fixtures(make_fixture(match_date=day, source_date=day))[0]
        data = {
            "fixture_id": fixture.fixture_id,
            "selection": "1x2_home",
            "our_selection": "home_win",
            "market_family": "1x2",
            "odds": 1.75,
            "reference_odds": 1.68,
            "consensus_odds": 1.65,
            "fair_odds": 1.7295,
            "p_model": 0.7319,
            "q_fair": 0.5782,
            "p_used": 0.5782,
            "rule_edge": 0.0046,
            "rule_qualified": True,
            "grade": "C",
            "stake_fraction": 0.00125,
            "config_status": "experimental",
            "selection_rule_status": "experimental",
        }
        data.update(overrides)
        with Session(engine) as db:
            db.add(StatPitchSelection(**data))
            db.commit()
        return fixture

    return _make


def _bet_day(engine, **overrides):
    data = {
        "match_date": today_local(),
        "count": 1,
        "assessed": 90,
        "qualified_by_rule": 1,
        "total_exposure": 0.00125,
        "caveat": UPSTREAM_CAVEAT,
        "selection_rule_status": "experimental",
        "config_status": "experimental",
        "disclaimer": "Simulation and analysis only.",
        "generated_at": datetime(2026, 9, 1, 6, 57, tzinfo=UTC),
    }
    data.update(overrides)
    with Session(engine) as db:
        db.add(StatPitchBetDay(**data))
        db.commit()


class TestCaveatFallback:
    def test_a_fitted_rule_needs_no_caveat(self):
        assert caveat_for("fitted") is None

    def test_an_experimental_rule_says_so(self):
        text = caveat_for("experimental")
        assert "experimental" in text
        assert "not yet" in text

    def test_an_unknown_status_is_still_qualified(self):
        """Silence would be the one unacceptable answer here."""
        assert caveat_for(None) is not None
        assert "unvalidated" in caveat_for(None)


class TestBuild:
    def test_a_staked_selection_is_returned_as_a_pick(self, engine, staked):
        fixture = staked()
        _bet_day(engine)

        with Session(engine) as db:
            result = build_bets_today(db, today_local(), "pro")

        assert result.count == 1
        pick = result.bets[0]
        assert pick.fixture_id == fixture.fixture_id
        assert pick.our_selection == "home_win"
        assert pick.stake_fraction == 0.00125

    def test_the_four_prices_survive_to_the_client(self, engine, staked):
        """Collapsing them would throw away the only evidence a reader has for
        whether the price is actually good."""
        staked()
        _bet_day(engine)

        with Session(engine) as db:
            pick = build_bets_today(db, today_local(), "pro").bets[0]

        assert (pick.odds, pick.reference_odds, pick.consensus_odds, pick.fair_odds) == (
            1.75,
            1.68,
            1.65,
            1.7295,
        )

    def test_the_pick_carries_its_fixture(self, engine, staked):
        """`/bets/today` is read without a fixture list beside it."""
        staked()
        _bet_day(engine)

        with Session(engine) as db:
            pick = build_bets_today(db, today_local(), "pro").bets[0]

        assert pick.home_team
        assert pick.away_team
        assert pick.competition_name
        assert pick.match_date == today_local()

    def test_an_unstaked_selection_is_not_a_pick(self, engine, staked):
        staked(stake_fraction=0.0)
        _bet_day(engine, count=0)

        with Session(engine) as db:
            result = build_bets_today(db, today_local(), "pro")

        assert result.bets == []
        assert result.count == 0

    def test_provenance_rides_on_every_pick(self, engine, staked):
        """Per row, not once per response: when the rule is promoted, a pick
        already shown must still read as what it was recommended under."""
        staked()
        _bet_day(engine)

        with Session(engine) as db:
            pick = build_bets_today(db, today_local(), "pro").bets[0]

        assert pick.selection_rule_status == "experimental"
        assert pick.config_status == "experimental"


class TestTheCaveatIsNeverMissing:
    def test_the_upstream_caveat_is_preferred(self, engine, staked):
        staked()
        _bet_day(engine)

        with Session(engine) as db:
            assert build_bets_today(db, today_local(), "pro").caveat == UPSTREAM_CAVEAT

    def test_a_pick_with_no_stored_caveat_still_gets_one(self, engine, staked):
        """The case this guard exists for: a sync that fetched the card but not
        `/bets/today` would otherwise serve a recommendation qualified by
        nothing at all."""
        staked()
        _bet_day(engine, caveat=None)

        with Session(engine) as db:
            result = build_bets_today(db, today_local(), "pro")

        assert result.bets
        assert result.caveat
        assert "experimental" in result.caveat

    def test_a_pick_with_no_bet_day_row_at_all_still_gets_one(self, engine, staked):
        """No day summary was ever stored, so the status comes off the row."""
        staked()

        with Session(engine) as db:
            result = build_bets_today(db, today_local(), "pro")

        assert result.bets
        assert result.caveat
        assert result.reason and "sync" in result.reason

    def test_an_empty_day_needs_no_caveat(self, engine):
        """Nothing has been claimed, so there is nothing to qualify."""
        _bet_day(engine, count=0, caveat=None, reason="Nothing to assess: card_is_empty.")

        with Session(engine) as db:
            result = build_bets_today(db, today_local(), "pro")

        assert result.bets == []
        assert result.caveat is None
        assert result.reason


class TestRoute:
    def test_pro_sees_the_pick_and_the_caveat(self, client, auth, engine, staked):
        staked()
        _bet_day(engine)

        body = client.get("/statpitch/bets/today", headers=auth).json()

        assert body["count"] == 1
        assert body["caveat"] == UPSTREAM_CAVEAT
        assert body["bets"][0]["stake_fraction"] == 0.00125

    def test_an_empty_day_is_a_200_not_a_404(self, client, auth):
        """The rule fires only where a book misprices; most days it does not."""
        response = client.get("/statpitch/bets/today", headers=auth)

        assert response.status_code == 200
        assert response.json()["bets"] == []

    def test_free_is_refused(self, client):
        """A staked recommendation is the edge indicator itself, so there is no
        reduced version worth returning."""
        assert client.get("/statpitch/bets/today").status_code == 402


class TestValueBetsBasis:
    def test_rule_returns_statpitchs_own_picks(self, client, auth, engine, staked):
        staked()

        body = client.get("/statpitch/fixtures/today/value-bets?basis=rule", headers=auth).json()

        assert len(body) == 1

    def test_the_default_is_still_ours(self, client, auth, engine, staked):
        """Repointing this endpoint at StatPitch's rule would rewrite what its
        numbers have always meant."""
        staked()

        body = client.get("/statpitch/fixtures/today/value-bets", headers=auth).json()

        # The fixture carries a StatPitch pick but no `best_overall_bet` of ours.
        assert body == []

    def test_an_unknown_basis_is_refused(self, client, auth):
        response = client.get("/statpitch/fixtures/today/value-bets?basis=nonsense", headers=auth)

        assert response.status_code == 422


class TestFixtureShape:
    def test_the_paid_shape_carries_the_selections(self, client, auth, engine, staked):
        staked()

        (fixture,) = client.get("/statpitch/fixtures/today", headers=auth).json()

        assert len(fixture["selections"]) == 1
        assert fixture["selections"][0]["selection"] == "1x2_home"
        assert fixture["selections"][0]["reference_odds"] == 1.68

    def test_an_unpriced_fixture_reports_an_empty_list(
        self, client, auth, seed_fixtures, make_fixture
    ):
        """Normal days ahead of kickoff — the feed publishes per matchday block."""
        seed_fixtures(make_fixture())

        (fixture,) = client.get("/statpitch/fixtures/today", headers=auth).json()

        assert fixture["selections"] == []

    def test_the_free_shape_does_not(self, client, engine, staked):
        """A price is half of "Book vs ML", which is a paid line."""
        staked()

        (fixture,) = client.get("/statpitch/fixtures/today").json()

        assert "selections" not in fixture


class TestStats:
    def test_rule_bets_are_counted_apart_from_ours(self, client, auth, engine, staked):
        """Two different strategies, not two views of one."""
        staked()

        body = client.get("/statpitch/stats", headers=auth).json()

        assert body["rule_bets_today"] == 1
        assert body["value_bets_today"] == 0

    def test_a_draw_pick_is_ranked_by_its_own_stake(
        self, client, auth, make_fixture, seed_fixtures
    ):
        """Not by `kelly_home`, which belongs to a different selection.

        The stronger pick here is a draw; ranking on the home column would sort
        it below a weaker home pick, or drop it to the bottom on a null.
        """
        seed_fixtures(
            make_fixture(
                home_team="Weak Home Pick",
                best_bet="home_win",
                kelly_home=0.03,
                kelly_draw=None,
            ),
            make_fixture(
                home_team="Strong Draw Pick",
                best_bet="draw",
                kelly_home=None,
                kelly_draw=0.09,
            ),
        )

        body = client.get("/statpitch/fixtures/today/value-bets?basis=1x2", headers=auth).json()

        assert [row["home_team"] for row in body] == ["Strong Draw Pick", "Weak Home Pick"]

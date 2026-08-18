"""Expected value, Kelly staking, and which pick each series takes."""

import pytest

from api.statpitch.pricing import (
    KELLY_FRACTION,
    MIN_KELLY,
    apply_pricing,
    expected_value,
    fractional_kelly,
    full_kelly,
    is_high_confidence,
    predicted_outcome,
)


class TestMath:
    def test_expected_value_is_zero_at_a_fair_price(self):
        assert expected_value(0.5, 2.0) == 0.0

    def test_expected_value_is_positive_when_the_price_is_generous(self):
        assert expected_value(0.6, 2.0) == pytest.approx(0.2)

    def test_full_kelly_is_zero_at_a_fair_price(self):
        assert full_kelly(0.5, 2.0) == 0.0

    def test_full_kelly_matches_the_textbook_figure(self):
        # 60% at evens: (0.6*2 - 1) / 1 = 0.2
        assert full_kelly(0.6, 2.0) == pytest.approx(0.2)

    def test_fractional_kelly_applies_the_fraction(self):
        assert fractional_kelly(0.6, 2.0) == pytest.approx(0.2 * KELLY_FRACTION)

    def test_no_edge_returns_none(self):
        assert fractional_kelly(0.4, 2.0) is None

    def test_edge_below_the_minimum_returns_none(self):
        stake = fractional_kelly(0.51, 2.0)
        assert stake is None

    def test_high_ev_low_probability_is_rejected(self):
        """The problem Kelly exists to solve.

        A 5% shot at 25.0 has +25% EV, which looks excellent, but its Kelly
        stake is tiny — well under the threshold worth taking.
        """
        assert expected_value(0.05, 25.0) > 0.2
        assert full_kelly(0.05, 25.0) * KELLY_FRACTION < MIN_KELLY
        assert fractional_kelly(0.05, 25.0) is None


class TestApplyPricing:
    def test_unpriced_fixture_gets_no_picks(self, make_fixture):
        fixture = make_fixture()
        apply_pricing(fixture)

        assert fixture.best_bet is None
        assert fixture.best_overall_bet is None
        assert fixture.ev_home is None
        assert fixture.kelly_home is None

    def test_prices_every_market_it_has_odds_for(self, make_fixture):
        fixture = make_fixture(odds_home=2.5, odds_draw=3.4, odds_away=3.0)
        apply_pricing(fixture)

        assert fixture.ev_home == pytest.approx(0.375)
        assert fixture.best_bet == "home_win"
        assert fixture.best_bet_odds == 2.5
        assert fixture.best_bet_prob == pytest.approx(0.55)

    def test_zero_and_negative_odds_are_ignored(self, make_fixture):
        # A 0.0 price means "not quoted"; treating it as real would produce a
        # nonsense EV of -1 and a bet we could never place.
        fixture = make_fixture(odds_home=0.0, odds_draw=1.0)
        apply_pricing(fixture)

        assert fixture.ev_home is None
        assert fixture.ev_draw is None

    def test_best_bet_stays_within_1x2(self, make_fixture):
        """The two series are different strategies and must not blur together."""
        fixture = make_fixture(
            odds_home=2.5,  # 55% -> clear edge
            odds_draw=3.4,
            odds_away=3.0,
            odds_over_2_5=2.6,  # 55% -> a bigger edge still
        )
        apply_pricing(fixture)

        assert fixture.best_bet == "home_win"
        assert fixture.best_overall_bet == "over_2_5"
        assert fixture.best_overall_kelly > 0

    def test_overall_falls_back_to_1x2_when_it_is_the_only_edge(self, make_fixture):
        fixture = make_fixture(odds_home=2.5, odds_draw=3.0, odds_away=3.0)
        apply_pricing(fixture)

        assert fixture.best_overall_bet == fixture.best_bet == "home_win"

    def test_under_probabilities_are_the_complement_of_over(self, make_fixture):
        # StatPitch publishes P(over) only; P(under) is derived.
        fixture = make_fixture(over_2_5=0.55, odds_under_2_5=3.0)
        apply_pricing(fixture)

        # 0.45 * 3.0 - 1 = 0.35
        assert fixture.ev_under_2_5 == pytest.approx(0.35)

    def test_repricing_clears_a_pick_that_no_longer_qualifies(self, make_fixture):
        fixture = make_fixture(odds_home=2.5, odds_draw=3.4, odds_away=3.0)
        apply_pricing(fixture)
        assert fixture.best_bet == "home_win"

        # The price shortens overnight and the edge evaporates.
        fixture.odds_home = 1.5
        apply_pricing(fixture)
        assert fixture.best_bet is None
        assert fixture.best_bet_odds is None


class TestReadings:
    def test_predicted_outcome_is_the_likeliest_not_the_best_bet(self, make_fixture):
        fixture = make_fixture(home_win_prob=0.20, draw_prob=0.25, away_win_prob=0.55)
        assert predicted_outcome(fixture) == "away_win"

    def test_high_confidence_ignores_the_draw(self, make_fixture):
        # A likely draw is not a confident match.
        fixture = make_fixture(home_win_prob=0.25, draw_prob=0.72, away_win_prob=0.03)
        assert not is_high_confidence(fixture)

    def test_high_confidence_on_a_strong_favourite(self, make_fixture):
        fixture = make_fixture(home_win_prob=0.75, draw_prob=0.15, away_win_prob=0.10)
        assert is_high_confidence(fixture)

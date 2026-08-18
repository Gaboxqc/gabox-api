"""Expected value and Kelly staking.

StatPitch deliberately never returns a bet: its shrinkage weight against the
closing line measured 0.000, and picking the largest edge per match measured
-2.12% ROI. Every selection in this file is therefore **ours**, derived from
StatPitch's probabilities and a real bookmaker price — which is also why the
ledger measures our strategy rather than StatPitch's.

    EV    = (probability x odds) - 1
    Kelly = (probability x odds - 1) / (odds - 1)

Kelly is the deciding number rather than EV, because EV alone cannot tell a
sound bet from a lottery ticket: +150% EV on a 5% shot has a tiny Kelly and is
not worth the variance.
"""

from collections.abc import Callable
from dataclasses import dataclass

from api.statpitch.models import StatPitchFixture

# Full Kelly is too aggressive to run in practice. Quarter Kelly picks the same
# selection, at a quarter of the theoretically optimal stake.
KELLY_FRACTION = 0.25

# Minimum fractional Kelly for a bet to be worth placing at all. Below this the
# edge may be real but the variance swamps it.
MIN_KELLY = 0.02

# A match is "high confidence" when either side is at least this likely. Draws
# are excluded on purpose: a high draw probability is not a confident match.
HIGH_CONFIDENCE_THRESHOLD = 0.70

ONE_X_TWO: tuple[str, ...] = ("home_win", "draw", "away_win")


@dataclass(frozen=True)
class Market:
    selection: str
    odds_field: str
    ev_field: str
    kelly_field: str
    # StatPitch publishes P(over) and P(both teams score) only; the complements
    # are derived here rather than stored twice.
    probability: Callable[[StatPitchFixture], float]


MARKETS: tuple[Market, ...] = (
    Market("home_win", "odds_home", "ev_home", "kelly_home", lambda f: f.home_win_prob),
    Market("draw", "odds_draw", "ev_draw", "kelly_draw", lambda f: f.draw_prob),
    Market("away_win", "odds_away", "ev_away", "kelly_away", lambda f: f.away_win_prob),
    Market("over_1_5", "odds_over_1_5", "ev_over_1_5", "kelly_over_1_5", lambda f: f.over_1_5),
    Market(
        "under_1_5", "odds_under_1_5", "ev_under_1_5", "kelly_under_1_5", lambda f: 1 - f.over_1_5
    ),
    Market("over_2_5", "odds_over_2_5", "ev_over_2_5", "kelly_over_2_5", lambda f: f.over_2_5),
    Market(
        "under_2_5", "odds_under_2_5", "ev_under_2_5", "kelly_under_2_5", lambda f: 1 - f.over_2_5
    ),
    Market("over_3_5", "odds_over_3_5", "ev_over_3_5", "kelly_over_3_5", lambda f: f.over_3_5),
    Market(
        "under_3_5", "odds_under_3_5", "ev_under_3_5", "kelly_under_3_5", lambda f: 1 - f.over_3_5
    ),
    Market("btts_yes", "odds_btts_yes", "ev_btts_yes", "kelly_btts_yes", lambda f: f.btts_yes),
    Market("btts_no", "odds_btts_no", "ev_btts_no", "kelly_btts_no", lambda f: f.btts_no),
)

_BY_SELECTION: dict[str, Market] = {market.selection: market for market in MARKETS}


def expected_value(probability: float, odds: float) -> float:
    """Average gain per unit staked."""
    return round((probability * odds) - 1, 4)


def full_kelly(probability: float, odds: float) -> float:
    """The mathematically optimal fraction of bankroll. Negative means no edge."""
    net_profit = odds - 1
    if net_profit <= 0:
        return 0.0
    return round((probability * odds - 1) / net_profit, 4)


def fractional_kelly(probability: float, odds: float) -> float | None:
    """Quarter Kelly, or None when the stake is too small to be worth taking."""
    staked = round(full_kelly(probability, odds) * KELLY_FRACTION, 4)
    return staked if staked >= MIN_KELLY else None


def market_for(selection: str) -> Market | None:
    return _BY_SELECTION.get(selection)


def probability_of(fixture: StatPitchFixture, selection: str) -> float | None:
    market = _BY_SELECTION.get(selection)
    return market.probability(fixture) if market else None


def odds_of(fixture: StatPitchFixture, selection: str) -> float | None:
    market = _BY_SELECTION.get(selection)
    return getattr(fixture, market.odds_field, None) if market else None


def apply_pricing(fixture: StatPitchFixture) -> None:
    """Fill in every EV and Kelly field, then choose both headline picks.

    Two picks are kept, not one. `best_bet` is the best 1X2 selection and
    `best_overall_bet` the best across every market — they are different
    strategies, and collapsing them into a single number would hide which one
    is actually earning.
    """
    candidates: dict[str, tuple[float, float, float, float]] = {}

    for market in MARKETS:
        odds = getattr(fixture, market.odds_field, None)
        probability = market.probability(fixture)

        if not odds or odds <= 1:
            setattr(fixture, market.ev_field, None)
            setattr(fixture, market.kelly_field, None)
            continue

        value = expected_value(probability, odds)
        stake = fractional_kelly(probability, odds)
        setattr(fixture, market.ev_field, value)
        setattr(fixture, market.kelly_field, stake)

        if stake is not None:
            candidates[market.selection] = (value, stake, odds, probability)

    _choose(fixture, candidates)


def _choose(
    fixture: StatPitchFixture, candidates: dict[str, tuple[float, float, float, float]]
) -> None:
    one_x_two = {name: data for name, data in candidates.items() if name in ONE_X_TWO}

    if one_x_two:
        best = max(one_x_two, key=lambda name: one_x_two[name][1])
        fixture.best_bet = best
        fixture.best_bet_odds = one_x_two[best][2]
        fixture.best_bet_prob = one_x_two[best][3]
    else:
        fixture.best_bet = None
        fixture.best_bet_odds = None
        fixture.best_bet_prob = None

    if candidates:
        best = max(candidates, key=lambda name: candidates[name][1])
        value, stake, odds, probability = candidates[best]
        fixture.best_overall_bet = best
        fixture.best_overall_ev = value
        fixture.best_overall_kelly = stake
        fixture.best_overall_odds = odds
        fixture.best_overall_prob = probability
    else:
        fixture.best_overall_bet = None
        fixture.best_overall_ev = None
        fixture.best_overall_kelly = None
        fixture.best_overall_odds = None
        fixture.best_overall_prob = None


def predicted_outcome(fixture: StatPitchFixture) -> str:
    """The most likely 1X2 result, regardless of whether it is a good bet."""
    outcomes = {
        "home_win": fixture.home_win_prob,
        "draw": fixture.draw_prob,
        "away_win": fixture.away_win_prob,
    }
    return max(outcomes, key=lambda name: outcomes[name])


def is_high_confidence(fixture: StatPitchFixture) -> bool:
    return max(fixture.home_win_prob, fixture.away_win_prob) >= HIGH_CONFIDENCE_THRESHOLD

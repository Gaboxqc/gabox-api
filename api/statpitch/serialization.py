"""Per-tier fixture shapes.

There are **two** shapes, not three. The pricing page gives Elite nothing Pro
does not already have except API access, so inventing an Elite-only data tier
here would be a rule the product does not actually sell.

    free  ->  identity, teams, crests, kickoff, 1X2 probabilities, result
    paid  ->  all of that, plus every market, price, edge and explanation

Gated fields are **absent**, never null. A `null` on `odds_over_2_5` is
indistinguishable from "no market was offered", and a frontend would render it
as missing data rather than as something worth paying for. Leaving the key out
entirely is unambiguous, and `locked` says why.
"""

from datetime import date, datetime
from typing import Any

from sqlmodel import SQLModel

from api.statpitch.accounts.models import Tier
from api.statpitch.models import StatPitchFixture
from api.statpitch.tiers import Feature, allows


class FixtureFreeRead(SQLModel):
    """What a free account sees: who is playing, when, and who is likely to win.

    Deliberately no bookmaker prices. "Market breakdown (Book vs ML)" is a paid
    line, and a price is half of that comparison — showing odds here would give
    away the more interesting half for nothing.
    """

    id: int
    fixture_id: str
    competition_id: str
    season: str | None
    stage: str | None
    format: str | None

    match_date: date
    source_date: date
    kickoff: str | None
    commence_time: datetime | None
    date_confirmed: bool

    home_team: str
    away_team: str
    neutral_venue: bool
    home_crest_url: str | None
    away_crest_url: str | None

    prediction_source: str | None
    model_version: str
    synced_at: datetime

    home_win_prob: float
    draw_prob: float
    away_win_prob: float

    home_score: int | None
    away_score: int | None
    actual_result: str | None

    # Tells the frontend it is looking at a reduced object, so an upsell can be
    # rendered where the numbers would be rather than an empty state.
    locked: bool = True


class FixtureFullRead(FixtureFreeRead):
    """Pro and Elite. Everything the model and the market have to say."""

    locked: bool = False

    odds_coverage: bool
    fully_rated: bool

    home_xg: float
    away_xg: float
    home_elo: float | None
    away_elo: float | None
    home_elo_source: str | None
    away_elo_source: str | None

    over_1_5: float
    over_2_5: float
    over_3_5: float
    btts_yes: float
    btts_no: float
    correct_scores: list[dict[str, Any]] | None
    explanation: dict[str, Any] | None

    odds_home: float | None
    odds_draw: float | None
    odds_away: float | None
    odds_over_1_5: float | None
    odds_under_1_5: float | None
    odds_over_2_5: float | None
    odds_under_2_5: float | None
    odds_over_3_5: float | None
    odds_under_3_5: float | None
    odds_btts_yes: float | None
    odds_btts_no: float | None

    ev_home: float | None
    ev_draw: float | None
    ev_away: float | None
    ev_over_1_5: float | None
    ev_under_1_5: float | None
    ev_over_2_5: float | None
    ev_under_2_5: float | None
    ev_over_3_5: float | None
    ev_under_3_5: float | None
    ev_btts_yes: float | None
    ev_btts_no: float | None

    kelly_home: float | None
    kelly_draw: float | None
    kelly_away: float | None
    kelly_over_1_5: float | None
    kelly_under_1_5: float | None
    kelly_over_2_5: float | None
    kelly_under_2_5: float | None
    kelly_over_3_5: float | None
    kelly_under_3_5: float | None
    kelly_btts_yes: float | None
    kelly_btts_no: float | None

    best_bet: str | None
    best_bet_odds: float | None
    best_bet_prob: float | None
    best_overall_bet: str | None
    best_overall_odds: float | None
    best_overall_prob: float | None
    best_overall_ev: float | None
    best_overall_kelly: float | None


# The paid shape needs every one of these; a tier missing any of them gets the
# free shape. Written as a set rather than a single flag so that moving one
# line between tiers on the pricing page is one edit in `tiers.POLICIES`.
_FULL_DEPTH = (Feature.MARKET_BREAKDOWN, Feature.EDGE_INDICATORS, Feature.CONFIDENCE)


def sees_full_depth(tier: Tier) -> bool:
    return all(allows(tier, feature) for feature in _FULL_DEPTH)


def serialize_fixture(fixture: StatPitchFixture, tier: Tier) -> FixtureFreeRead:
    """One fixture, shaped for the caller.

    The return type is the free model because it is the common ancestor; the
    paid shape is a subclass, so FastAPI serialises whichever it is actually
    handed.
    """
    model = FixtureFullRead if sees_full_depth(tier) else FixtureFreeRead
    return model.model_validate(fixture, from_attributes=True)


def serialize_fixtures(fixtures: list[StatPitchFixture], tier: Tier) -> list[FixtureFreeRead]:
    """The list form, resolving the shape once rather than per fixture."""
    model = FixtureFullRead if sees_full_depth(tier) else FixtureFreeRead
    return [model.model_validate(fixture, from_attributes=True) for fixture in fixtures]

"""Confidence bands.

The claim being tested is mostly a negative one: a decisive-looking number built
on weak inputs must not read as high confidence. That is the failure mode a
subscriber would actually be hurt by, so most of these push on it.
"""

import pytest
from sqlmodel import Session, select

from api.statpitch.confidence import FITTED_MODEL_SOURCE, MEASURED_ELO_SOURCE, assess
from api.statpitch.pricing import HIGH_CONFIDENCE_THRESHOLD
from api.statpitch.serialization import serialize_fixture


def _assess(**overrides):
    """A clean, decisive, fully corroborated prediction unless told otherwise."""
    defaults = dict(
        prediction_source=FITTED_MODEL_SOURCE,
        fully_rated=True,
        home_elo_source=MEASURED_ELO_SOURCE,
        away_elo_source=MEASURED_ELO_SOURCE,
        home_win_prob=0.80,
        away_win_prob=0.10,
        has_price=True,
    )
    return assess(**{**defaults, **overrides})


# ── High ─────────────────────────────────────────────────────────────────────


def test_clean_inputs_and_a_decisive_call_are_high():
    result = _assess()
    assert result.band == "high"
    assert result.reasons


def test_an_away_favourite_counts_as_decisive():
    assert _assess(home_win_prob=0.10, away_win_prob=0.80).band == "high"


def test_exactly_at_the_threshold_still_counts():
    assert _assess(home_win_prob=HIGH_CONFIDENCE_THRESHOLD, away_win_prob=0.15).band == "high"


# ── The veto: weak inputs override a confident-looking number ────────────────


def test_an_unrated_club_is_low_however_decisive_the_number_looks():
    """The failure mode worth guarding. A 0.95 built on a prior is *less*
    trustworthy than a 0.72 built on two measured sides, not more."""
    result = _assess(fully_rated=False, home_win_prob=0.95, away_win_prob=0.02)

    assert result.band == "low"
    assert "no measured Elo" in result.reasons[0]


def test_the_fallback_model_is_low():
    result = _assess(prediction_source="elo-poisson", home_win_prob=0.90)

    assert result.band == "low"
    assert "elo-poisson" in result.reasons[0]


def test_both_problems_are_both_reported():
    result = _assess(fully_rated=False, prediction_source="elo-poisson")

    assert result.band == "low"
    assert len(result.reasons) == 2


def test_an_unknown_prediction_source_is_not_punished():
    """`None` means StatPitch did not say, which is not the same as saying it
    used the weaker path. Guessing the worst would band most of the cache low on
    no evidence."""
    assert _assess(prediction_source=None).band == "high"


# ── Medium ───────────────────────────────────────────────────────────────────


def test_an_open_match_on_good_data_is_medium():
    result = _assess(home_win_prob=0.40, away_win_prob=0.35)

    assert result.band == "medium"
    assert any("open" in reason for reason in result.reasons)


def test_a_prior_rated_side_without_the_fully_rated_flag_is_medium():
    """`fully_rated` is StatPitch's own summary; the per-side sources are finer
    grained. A disagreement between them should cost some confidence, not all."""
    result = _assess(home_elo_source="pooled_prior")

    assert result.band == "medium"
    assert any("measured Elo" in reason for reason in result.reasons)


def test_no_price_is_medium():
    result = _assess(has_price=False)

    assert result.band == "medium"
    assert any("bookmaker price" in reason for reason in result.reasons)


def test_a_missing_elo_source_is_treated_as_unmeasured():
    assert _assess(away_elo_source=None).band == "medium"


def test_every_medium_says_why():
    for overrides in (
        {"home_win_prob": 0.40, "away_win_prob": 0.35},
        {"has_price": False},
        {"home_elo_source": "default"},
    ):
        result = _assess(**overrides)
        assert result.band == "medium"
        assert result.reasons


# ── Consistency with the existing stat ───────────────────────────────────────


def test_the_band_agrees_with_the_high_confidence_threshold():
    """`high_confidence_today` in /stats uses the same constant. Two notions of
    "confident" telling different stories on the same page would be worse than
    having none."""
    just_under = _assess(home_win_prob=HIGH_CONFIDENCE_THRESHOLD - 0.01, away_win_prob=0.15)
    just_over = _assess(home_win_prob=HIGH_CONFIDENCE_THRESHOLD + 0.01, away_win_prob=0.15)

    assert just_under.band == "medium"
    assert just_over.band == "high"


# ── On the fixture and over the wire ─────────────────────────────────────────


def test_a_fixture_reports_its_own_band(make_fixture):
    fixture = make_fixture(
        fully_rated=False, home_win_prob=0.90, draw_prob=0.05, away_win_prob=0.05
    )
    assert fixture.confidence == "low"
    assert fixture.confidence_reasons


def _high_confidence(make_fixture, **overrides):
    """A fixture with everything the top band requires."""
    return make_fixture(
        prediction_source=FITTED_MODEL_SOURCE,
        fully_rated=True,
        home_elo_source=MEASURED_ELO_SOURCE,
        away_elo_source=MEASURED_ELO_SOURCE,
        home_win_prob=0.80,
        draw_prob=0.12,
        away_win_prob=0.08,
        odds_home=1.45,
        **overrides,
    )


def test_a_fixture_with_no_ratings_or_price_is_medium(make_fixture):
    """The common case early in a sync: predictions have landed, odds have not,
    and StatPitch has not said where the ratings came from."""
    assert make_fixture().confidence == "medium"


def test_the_band_is_derived_not_stored(engine, make_fixture, seed_fixtures):
    """A column would be a second copy that could fall out of step with the
    numbers it describes."""
    from api.statpitch.models import StatPitchFixture

    seed_fixtures(_high_confidence(make_fixture))
    with Session(engine) as db:
        row = db.exec(select(StatPitchFixture)).first()
        assert row.confidence == "high"

        row.fully_rated = False
        # No commit, no recompute step — the band simply follows.
        assert row.confidence == "low"


@pytest.mark.parametrize("tier", ["pro", "elite"])
def test_paid_tiers_see_the_band(tier, make_fixture, seed_fixtures):
    (fixture,) = seed_fixtures(make_fixture())
    payload = serialize_fixture(fixture, tier).model_dump()

    assert payload["confidence"] in {"low", "medium", "high"}
    assert isinstance(payload["confidence_reasons"], list)


def test_free_does_not_see_the_band(make_fixture, seed_fixtures):
    """ "AI confidence scoring" is a Pro line on the pricing page."""
    (fixture,) = seed_fixtures(make_fixture())
    payload = serialize_fixture(fixture, "free", unlocked=True).model_dump()

    assert "confidence" not in payload
    assert "confidence_reasons" not in payload


def test_it_reaches_the_endpoint(client, auth, make_fixture, seed_fixtures):
    seed_fixtures(make_fixture())
    (body,) = client.get("/statpitch/fixtures/today", headers=auth).json()

    assert body["confidence"] in {"low", "medium", "high"}
    assert body["confidence_reasons"]

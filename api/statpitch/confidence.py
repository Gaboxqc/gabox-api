"""How much weight a prediction deserves — low, medium or high.

Two different things get called "confidence", and conflating them is the trap
this module exists to avoid:

- **Decisiveness** — how one-sided the model's call is. That is
  `HIGH_CONFIDENCE_THRESHOLD` in `pricing`, and it already drives the
  `high_confidence_today` stat.
- **Trustworthiness** — how much the inputs behind that call are worth.

A 0.85 home win built on a club that has no measured Elo is a *less* reliable
number than a 0.72 built on two properly rated sides, even though it looks more
impressive. Reporting it as high confidence would be selling a subscriber the
opposite of what they are paying for.

So the band needs both axes, and data quality is allowed to veto:

    low     the model cannot support a strong claim — a club fell back to a
            prior, or the weaker elo-poisson path produced the numbers
    high    clean inputs *and* a decisive call *and* a market price agreeing
            that a market exists
    medium  everything else

Bands rather than a 0-100 score on purpose. A score implies a precision that
four boolean-ish inputs cannot justify, and invites people to read a difference
between 71 and 68 that is not there.

Nothing here imports the fixture model: it takes the values it needs, so
`models` can expose this as a property without a circular import.
"""

from dataclasses import dataclass, field
from typing import Literal

from api.statpitch.pricing import HIGH_CONFIDENCE_THRESHOLD

Band = Literal["low", "medium", "high"]

# The one Elo source that is actually measured. Everything else — entrant_prior,
# pooled_prior, default — is a stand-in for a club we have no history on.
MEASURED_ELO_SOURCE = "club_elo"

# StatPitch's own name for the fitted model. Anything else is the elo-poisson
# fallback, which its documentation describes as measurably weaker.
FITTED_MODEL_SOURCE = "fitted_goal_model"


@dataclass(frozen=True)
class Assessment:
    band: Band
    # Short, human-readable, and ordered most-important-first. A band with no
    # explanation is hard to trust and impossible to argue with.
    reasons: list[str] = field(default_factory=list)


def assess(
    *,
    prediction_source: str | None,
    fully_rated: bool,
    home_elo_source: str | None,
    away_elo_source: str | None,
    home_win_prob: float,
    away_win_prob: float,
    has_price: bool,
) -> Assessment:
    """Band this prediction, with the reasons that decided it."""
    reasons: list[str] = []

    # ── Vetoes: the model itself is on shaky ground ──────────────────────────
    if not fully_rated:
        reasons.append("A club has no measured Elo rating, so a prior stood in for it.")
    if prediction_source is not None and prediction_source != FITTED_MODEL_SOURCE:
        reasons.append("Produced by the fallback elo-poisson model rather than the fitted one.")

    if reasons:
        return Assessment(band="low", reasons=reasons)

    # ── Otherwise, how decisive is it and how well corroborated? ─────────────
    decisive = max(home_win_prob, away_win_prob) >= HIGH_CONFIDENCE_THRESHOLD
    both_measured = (
        home_elo_source == MEASURED_ELO_SOURCE and away_elo_source == MEASURED_ELO_SOURCE
    )

    if not both_measured:
        reasons.append("At least one club's rating did not come from measured Elo.")
    if not decisive:
        reasons.append(f"No outcome clears {HIGH_CONFIDENCE_THRESHOLD:.0%}, so the match is open.")
    if not has_price:
        reasons.append("No bookmaker price to check the model against.")

    if not reasons:
        return Assessment(
            band="high",
            reasons=[
                "Both clubs have measured Elo ratings.",
                f"The model puts one outcome above {HIGH_CONFIDENCE_THRESHOLD:.0%}.",
                "A bookmaker price is available to compare against.",
            ],
        )

    return Assessment(band="medium", reasons=reasons)

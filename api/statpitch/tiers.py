"""What each tier is allowed to see.

One table, in one file, so the answer to "is this Pro-only?" is never spread
across a dozen route handlers. Everything that gates on a tier reads from here.

Three kinds of gate, because the pricing page sells three kinds of thing:

- **A quota** — free accounts get three predictions a day.
- **A scope** — free accounts see the five priced leagues, paid ones all twelve.
- **Features** — market breakdown, edge indicators, confidence, the ledger, API
  access.

Only the last is a boolean, which is why this is a dataclass rather than a set
of feature flags.

A note on how the gates are enforced: depth is a *response shape*, not a 403.
Free callers get a smaller object, not an error, because "upgrade to see the
numbers you already know exist" is the product working as intended. Whole
endpoints — the ledger, the ROI stats — are the exception: there is no partial
version of a track record worth returning.
"""

from dataclasses import dataclass
from enum import StrEnum

from api.statpitch.accounts.models import TIER_ORDER, Tier
from api.statpitch.leagues import ALL_COMPETITIONS, STATPITCH_ODDS_COVERAGE


class Feature(StrEnum):
    """Named for the line it corresponds to on the pricing page, so the two
    cannot drift without somebody noticing."""

    # Bookmaker prices beside the model's own probabilities — "Book vs ML".
    MARKET_BREAKDOWN = "market_breakdown"
    # Expected value, Kelly stakes, and the picks derived from them.
    EDGE_INDICATORS = "edge_indicators"
    # How much weight the model's own numbers deserve on this fixture.
    CONFIDENCE = "confidence"
    # The settled-bet ledger and the rolling ROI built from it.
    LEDGER_ROI = "ledger_roi"
    # Programmatic access with an account-scoped key.
    API_ACCESS = "api_access"


@dataclass(frozen=True)
class TierPolicy:
    # None means unlimited.
    daily_predictions: int | None
    # None means every competition. Anything else is the allowed set.
    competitions: frozenset[str] | None
    features: frozenset[Feature]

    def allows(self, feature: Feature) -> bool:
        return feature in self.features

    def permits_competition(self, competition_id: str) -> bool:
        return self.competitions is None or competition_id in self.competitions


_PRO_FEATURES = frozenset(
    {
        Feature.MARKET_BREAKDOWN,
        Feature.EDGE_INDICATORS,
        Feature.CONFIDENCE,
        Feature.LEDGER_ROI,
    }
)

POLICIES: dict[Tier, TierPolicy] = {
    # "3 predictions per day", "The 5 priced leagues only", "1X2 win
    # probabilities", "Match of the Day pick".
    #
    # The competition set is `STATPITCH_ODDS_COVERAGE` rather than a second list
    # of the same five: "the leagues we can price" and "the leagues free sees"
    # are the same idea, and writing it twice invites them to diverge.
    "free": TierPolicy(
        daily_predictions=3,
        competitions=STATPITCH_ODDS_COVERAGE,
        features=frozenset(),
    ),
    # "Unlimited predictions", "All 12 competitions", plus everything except the
    # API.
    "pro": TierPolicy(
        daily_predictions=None,
        competitions=None,
        features=_PRO_FEATURES,
    ),
    # Pro, plus programmatic access.
    "elite": TierPolicy(
        daily_predictions=None,
        competitions=None,
        features=_PRO_FEATURES | {Feature.API_ACCESS},
    ),
}


def policy_for(tier: Tier) -> TierPolicy:
    """The policy for a tier, defaulting to the most restrictive one.

    An unrecognised tier — a typo in a manual grant, a value from a future
    version read by an older deploy — resolves to `free` rather than raising.
    Failing closed is the only safe direction here.
    """
    return POLICIES.get(tier, POLICIES["free"])


def allows(tier: Tier, feature: Feature) -> bool:
    return policy_for(tier).allows(feature)


def visible_competitions(tier: Tier) -> frozenset[str]:
    """The competitions a tier may see, always as a concrete set."""
    policy = policy_for(tier)
    return ALL_COMPETITIONS if policy.competitions is None else policy.competitions


def is_at_least(tier: Tier, minimum: Tier) -> bool:
    """Whether `tier` ranks at or above `minimum`.

    For the handful of places that genuinely mean "Pro or better" rather than a
    named feature — an upsell message, say. Prefer `allows()`: a feature moving
    between tiers should be one edit to `POLICIES`, not a hunt for comparisons.
    """
    order = list(TIER_ORDER)
    try:
        return order.index(tier) >= order.index(minimum)
    except ValueError:
        return False

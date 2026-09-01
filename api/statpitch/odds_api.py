"""What is left of The Odds API: credentials, and its one remaining error.

This module used to be `odds_service`, and it used to be where every price in
the product came from. It is not any more. StatPitch prices its own card now,
against a 25-book panel with a Pinnacle-class benchmark, published per fixture
and keyed by `fixture_id` — so prices arrive already joined, at no quota cost,
and with a reference and consensus this could never supply.

What survives is results. StatPitch has no results endpoint — none of its routes
return what a match finished — so `scores_service` is still the only thing that
can settle a bet, and it is still billed against the same 500/month free tier.
Scores alone is roughly 150 requests a month across five leagues, well inside it
now that no market requests compete for the budget.

`OddsUnavailable` keeps its name deliberately. It has always meant "The Odds API
is not usable", not "there are no odds", and it is raised and caught in three
other modules that would gain nothing from a rename.
"""

from api.core.config import settings


class OddsUnavailable(RuntimeError):
    """The Odds API is not configured, or refused the credentials."""


def api_key() -> str:
    key = settings.odds_api_key.strip()
    if not key:
        raise OddsUnavailable("ODDS_API_KEY is not configured on the server.")
    return key

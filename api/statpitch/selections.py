"""Translating StatPitch's selection names into ours.

The two vocabularies disagree, and the disagreement is not cosmetic:

    1x2_home  ->  home_win
    1x2_draw  ->  draw
    1x2_away  ->  away_win

Ours are the names the ledger has settled bets under and `selection_won` knows
how to resolve, so they cannot be changed without rewriting history. StatPitch's
are what arrives on every priced row. One table, in one file, so a mapping is
never re-guessed at a call site.

Totals and handicaps are deliberately absent. StatPitch publishes only the 1X2
family today — `market_families: ["1x2"]` — and the shape of a totals selection
(whether the line rides in the name, in `line`, or both) cannot be settled by
looking at a service that does not emit one yet. `translate` returns None for
anything unknown, which is the safe direction: an untranslatable selection is
stored for display and never priced, staked or settled.
"""

# StatPitch's 1X2 names, which are the only ones it currently publishes.
STATPITCH_TO_OURS: dict[str, str] = {
    "1x2_home": "home_win",
    "1x2_draw": "draw",
    "1x2_away": "away_win",
}

OURS_TO_STATPITCH: dict[str, str] = {ours: theirs for theirs, ours in STATPITCH_TO_OURS.items()}

# The only family that can currently produce a bet. StatPitch confines its rule
# to 1X2 because picking the best edge *across* markets measured -2.12% ROI
# against +0.13% for committing to one.
#
# This is also a settlement guard. `selection_won` resolves every market on
# halves, so nothing it knows about can push — but a handicap can, and a
# handicap reaching it would settle a push as a loss. Nothing outside this set
# is ever banked.
BANKABLE_FAMILIES: frozenset[str] = frozenset({"1x2"})


def translate(selection: str) -> str | None:
    """Our name for a StatPitch selection, or None if we cannot price it."""
    return STATPITCH_TO_OURS.get(selection)


def is_bankable(market_family: str) -> bool:
    """Whether a settled row in this family may reach the ledger."""
    return market_family in BANKABLE_FAMILIES

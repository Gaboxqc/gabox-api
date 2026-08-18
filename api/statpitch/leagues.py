"""StatPitch competitions mapped to The Odds API sport keys.

StatPitch covers twelve competitions and reports `odds_coverage` for five of
them. That flag describes *StatPitch's* own odds source, not ours — we price
from The Odds API independently, so a competition StatPitch marks uncovered can
still be priced here if a sport key exists for it.

Every request costs quota, though, so `settings.statpitch_competitions` decides
what actually gets synced. The five leagues are the default.
"""

# Competitions StatPitch itself can price. Kept as a set so the sync can flag a
# mismatch between what we ask for and what StatPitch believes it covers.
STATPITCH_ODDS_COVERAGE: frozenset[str] = frozenset(
    {
        "ENG.PL",
        "ESP.LALIGA",
        "GER.BUNDESLIGA",
        "ITA.SERIEA",
        "FRA.LIGUE1",
    }
)

# The Odds API sport key per competition. Copa del Rey and Coupe de France are
# absent on purpose: no stable key exists for them, so they would only ever
# return predictions with no price attached.
COMPETITION_SPORT_KEYS: dict[str, str] = {
    "ENG.PL": "soccer_epl",
    "ESP.LALIGA": "soccer_spain_la_liga",
    "GER.BUNDESLIGA": "soccer_germany_bundesliga",
    "ITA.SERIEA": "soccer_italy_serie_a",
    "FRA.LIGUE1": "soccer_france_ligue_one",
    "ENG.FA_CUP": "soccer_fa_cup",
    "GER.DFB_POKAL": "soccer_germany_dfb_pokal",
    "ITA.COPPA_ITALIA": "soccer_italy_coppa_italia",
    "UEFA.UCL": "soccer_uefa_champs_league",
    "UEFA.UEL": "soccer_uefa_europa_league",
}

ALL_COMPETITIONS: frozenset[str] = frozenset(
    {
        "ENG.PL",
        "ESP.LALIGA",
        "GER.BUNDESLIGA",
        "ITA.SERIEA",
        "FRA.LIGUE1",
        "ENG.FA_CUP",
        "ESP.COPA_DEL_REY",
        "GER.DFB_POKAL",
        "ITA.COPPA_ITALIA",
        "FRA.COUPE_DE_FRANCE",
        "UEFA.UCL",
        "UEFA.UEL",
    }
)


def sport_key_for(competition_id: str) -> str | None:
    return COMPETITION_SPORT_KEYS.get(competition_id)

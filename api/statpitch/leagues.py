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

# ESPN's league slug per competition, used only to seed club crests.
#
# ESPN publishes a team list per league at
# `site.api.espn.com/apis/site/v2/sports/soccer/{slug}/teams`, which carries a
# transparent PNG for every club plus its short name and abbreviation. It needs
# no key, and coverage of the five leagues and both UEFA competitions is total —
# 168 clubs, none missing a badge. The cups have gaps, all of them amateur and
# lower-division sides in the early rounds, which no free source covers either.
#
# The endpoint is undocumented, which is exactly why the crest bytes are copied
# into our own storage rather than hotlinked: this is a seeding-time dependency,
# not a runtime one, and if it disappears the crests already fetched keep
# serving.
ESPN_LEAGUE_SLUGS: dict[str, str] = {
    "ENG.PL": "eng.1",
    "ESP.LALIGA": "esp.1",
    "GER.BUNDESLIGA": "ger.1",
    "ITA.SERIEA": "ita.1",
    "FRA.LIGUE1": "fra.1",
    "ENG.FA_CUP": "eng.fa",
    "ESP.COPA_DEL_REY": "esp.copa_del_rey",
    "GER.DFB_POKAL": "ger.dfb_pokal",
    "ITA.COPPA_ITALIA": "ita.coppa_italia",
    "FRA.COUPE_DE_FRANCE": "fra.coupe_de_france",
    "UEFA.UCL": "uefa.champions",
    "UEFA.UEL": "uefa.europa",
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

"""StatPitch competitions, and how each maps onto ESPN.

Twelve competitions, five of them synced by default via
`settings.statpitch_competitions`. StatPitch reports `odds_coverage` for the
same five, but that flag describes *its* odds source rather than ours.

There is one external mapping left. Prices come from StatPitch keyed by
`fixture_id`, so nothing has to be looked up for them — but final scores and
club crests both come from ESPN, and both are addressed by the same league slug.
One mapping, two consumers.
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

# ESPN's league slug per competition, used for club crests and final scores.
#
# ESPN publishes a team list per league at
# `site.api.espn.com/apis/site/v2/sports/soccer/{slug}/teams`, which carries a
# transparent PNG for every club plus its short name and abbreviation. It needs
# no key, and coverage of the five leagues and both UEFA competitions is total —
# 168 clubs, none missing a badge. The cups have gaps, all of them amateur and
# lower-division sides in the early rounds, which no free source covers either.
#
# The endpoint is undocumented, which is exactly why the crest bytes are copied
# into our own storage rather than hotlinked: if it disappears the crests
# already fetched keep serving.
#
# The `/scoreboard` endpoint under the same slug is what settles fixtures. That
# one *is* a runtime dependency, and unlike the crests it cannot be cached
# ahead of time — a result does not exist until the match is played. It is
# keyless and unmetered, and a failure is treated as a warning rather than an
# error, so a bad day there costs a settlement run and nothing more.
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


def espn_slug_for(competition_id: str) -> str | None:
    """The ESPN league slug, used for both crests and final scores."""
    return ESPN_LEAGUE_SLUGS.get(competition_id)


# How each competition is named, as ESPN names it. `(name, short_name)`.
#
# The long form is what a page heading wants; the short form is what fits in a
# filter chip beside a crest. Both are taken from ESPN rather than invented, so
# they agree with the icons that come from the same place — and so nobody has to
# decide whether it is "LaLiga", "La Liga" or "LALIGA".
COMPETITION_NAMES: dict[str, tuple[str, str]] = {
    "ENG.PL": ("English Premier League", "Premier League"),
    "ESP.LALIGA": ("Spanish LALIGA", "LALIGA"),
    "GER.BUNDESLIGA": ("German Bundesliga", "Bundesliga"),
    "ITA.SERIEA": ("Italian Serie A", "Serie A"),
    "FRA.LIGUE1": ("French Ligue 1", "Ligue 1"),
    "ENG.FA_CUP": ("English FA Cup", "FA Cup"),
    "ESP.COPA_DEL_REY": ("Spanish Copa del Rey", "Copa del Rey"),
    "GER.DFB_POKAL": ("German Cup", "DFB-Pokal"),
    "ITA.COPPA_ITALIA": ("Coppa Italia", "Coppa Italia"),
    "FRA.COUPE_DE_FRANCE": ("Coupe de France", "Coupe de France"),
    "UEFA.UCL": ("UEFA Champions League", "Champions League"),
    "UEFA.UEL": ("UEFA Europa League", "Europa League"),
}

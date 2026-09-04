"""StatPitch competitions, and how each maps onto ESPN.

Fifteen competitions. There is one external mapping left: prices come from
StatPitch keyed by `fixture_id`, so nothing has to be looked up for them — but
final scores and club crests both come from ESPN, and both are addressed by the
same league slug. One mapping, two consumers.

**Four sets, and they are not interchangeable.** They were, once. Until the
Primeira Liga, Eredivisie and Süper Lig arrived, "everything served", "what has
a price", "what can produce a bet" and "what a free account sees" all named the
same five leagues, and this file held a single constant with a comment claiming
they were one idea. They are not, and collapsing them again is how the free tier
silently gains a league nobody decided to give away:

    ALL_COMPETITIONS   15   everything StatPitch serves
    PRICED_LEAGUES      8   `odds_coverage: true` — has a market
    STAKEABLE_LEAGUES   6   measured to earn, so a bet is possible
    FREE_TIER_LEAGUES   5   a promise on the pricing page

Each is written out in full rather than derived from another, because every
derivation that looked safe is exactly what broke.
"""

# Competitions StatPitch can price. Kept as a set so the sync can flag a
# mismatch between what we ask for and what StatPitch believes it covers.
PRICED_LEAGUES: frozenset[str] = frozenset(
    {
        "ENG.PL",
        "ESP.LALIGA",
        "GER.BUNDESLIGA",
        "ITA.SERIEA",
        "FRA.LIGUE1",
        "POR.PRIMEIRA",
        "NED.EREDIVISIE",
        "TUR.SUPERLIG",
    }
)

# Where the selection rule has been *measured* to earn. A priced league is not
# automatically a bettable one: the Eredivisie's own CLV estimate is negative
# (-0.22%, t=-0.82) and the Primeira Liga's is positive but unresolvable at
# n=974 (+0.27%, t=+1.07). Both are served in full — fixtures, predictions,
# prices — and neither can produce a bet.
#
# This is a **fallback**, not the authority. The scope is re-measured upstream
# and published per day on `/bets/today` as `selection_rule.competitions`; the
# Primeira Liga is explicitly expected to be reconsidered. Read the stored value
# where one exists and only fall back to this for a database that has never
# synced — hardcoding it would mean an upstream re-measurement and our UI
# quietly disagreeing.
STAKEABLE_LEAGUES: frozenset[str] = frozenset(
    {
        "ENG.PL",
        "ESP.LALIGA",
        "GER.BUNDESLIGA",
        "ITA.SERIEA",
        "FRA.LIGUE1",
        "TUR.SUPERLIG",
    }
)

# What a free account sees. Pinned by hand, and deliberately not derived from
# any of the sets above.
#
# It used to be `PRICED_LEAGUES` on the reasoning that "the leagues we can
# price" and "the leagues free sees" were the same idea. They coincided; they
# were never the same idea. One is a fact about our data sources and the other
# is a commitment on the pricing page — "The 5 priced leagues only" — and
# leaving them joined meant the next league we could price became a giveaway.
# Widening this is a pricing decision, made here on purpose.
FREE_TIER_LEAGUES: frozenset[str] = frozenset(
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
    "POR.PRIMEIRA": "por.1",
    "NED.EREDIVISIE": "ned.1",
    "TUR.SUPERLIG": "tur.1",
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
        "POR.PRIMEIRA",
        "NED.EREDIVISIE",
        "TUR.SUPERLIG",
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
# Note the ID prefixes on the three newest: `POR`/`NED`/`TUR` are Club Elo ISO-3
# country codes, not the ISO-2 codes the rest of the world uses. They are
# StatPitch's identifiers, opaque to us, and must match exactly — "PT" would
# simply not resolve.
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
    "POR.PRIMEIRA": ("Portuguese Primeira Liga", "Primeira Liga"),
    "NED.EREDIVISIE": ("Dutch Eredivisie", "Eredivisie"),
    "TUR.SUPERLIG": ("Turkish Super Lig", "Super Lig"),
    "ENG.FA_CUP": ("English FA Cup", "FA Cup"),
    "ESP.COPA_DEL_REY": ("Spanish Copa del Rey", "Copa del Rey"),
    "GER.DFB_POKAL": ("German Cup", "DFB-Pokal"),
    "ITA.COPPA_ITALIA": ("Coppa Italia", "Coppa Italia"),
    "FRA.COUPE_DE_FRANCE": ("Coupe de France", "Coupe de France"),
    "UEFA.UCL": ("UEFA Champions League", "Champions League"),
    "UEFA.UEL": ("UEFA Europa League", "Europa League"),
}

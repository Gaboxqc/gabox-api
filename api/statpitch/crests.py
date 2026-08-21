"""Resolving club crests from ESPN, and normalising the bytes.

ESPN publishes a team list per league that needs no key and carries a
transparent PNG for every club, plus three name variants to match against.
Coverage of the five priced leagues and both UEFA competitions is total — 168
clubs, none missing a badge. The cups have gaps, all of them amateur and
lower-division sides in the early rounds, which no free source covers either.

**Matching a single club name is harder than matching a fixture**, and this
module is careful about it in a way `matching.best_match` does not have to be.
That function scores a *pair* of clubs, and the away side breaks ties;
"RCD Espanyol de Barcelona" cannot be mistaken for Barcelona when the opponent
has to agree too. Here there is no pair — just one name against one league — so
two extra defences stand in for the missing context:

1. **Candidates are confined to one competition.** Twenty clubs, not four
   hundred.
2. **The winner must clear the runner-up by a margin.** A close call is reported
   as ambiguous and left unresolved, because the wrong crest is materially worse
   than no crest: a monogram reads as "not loaded yet", while Barcelona's badge
   on an Espanyol fixture reads as a broken product.

Nothing here imports Pillow or boto3 at module scope, so the module stays
importable in the API without either dependency installed.
"""

import logging
from dataclasses import dataclass

import httpx

from api.statpitch.matching import similarity

log = logging.getLogger("statpitch.crests")

ESPN_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/teams"

# Resemblance a name must reach before its crest is considered at all.
MIN_CREST_SIMILARITY = 0.72
# ...and how far it must beat the runner-up. Without this, a league containing
# both "Espanyol" and "Barcelona" resolves the wrong way round roughly whenever
# the registry holds the long form of the name.
MIN_CREST_MARGIN = 0.08

# ESPN's source art is 500x500, and that is the real ceiling — the 1000px
# "combiner" URL is only an upscale of the same pixels.
#
# 512 therefore never resamples: the cropped badge fits inside it untouched, so
# the original crisp pixels survive and lossless WebP compresses flat logo art
# down to ~14KB — smaller than a 256px *lossy* encode, and sharper than
# anything. 128 stays for dense fixture lists, where forty 14KB files would be
# 560KB of badges nobody is looking closely at.
CREST_SIZES: tuple[int, ...] = (512, 128)

# Which size a `crest_url` points at. Native resolution, so a crest is sharp
# wherever it is rendered and on any pixel density, at roughly 25KB.
#
# The cheaper 128px file (~7KB) is still there and is reachable by swapping the
# suffix — every size of a club shares one hash, see `crest_key`. A dense
# fixture list is the case that wants it: forty native-size badges is about a
# megabyte, against 280KB.
DEFAULT_CREST_SIZE = 512


@dataclass(frozen=True)
class EspnTeam:
    """One club as ESPN describes it."""

    espn_id: str
    display_name: str
    short_name: str
    abbreviation: str
    logo_url: str | None
    # ESPN keeps a second badge tuned for dark backgrounds, and it genuinely
    # differs for most clubs. StatPitch's UI is near-black, so this is the one
    # that matters — but it is not published for every club, hence nullable.
    dark_logo_url: str | None

    @property
    def names(self) -> tuple[str, ...]:
        """Every spelling worth scoring against.

        The registry may hold the registered name ("Club Atletico de Madrid")
        while ESPN holds the trading name ("Atletico Madrid") — or the reverse.
        Scoring all three and taking the best is what bridges that.
        """
        return tuple(
            name for name in (self.display_name, self.short_name, self.abbreviation) if name
        )

    @property
    def best_logo_url(self) -> str | None:
        return self.dark_logo_url or self.logo_url


@dataclass(frozen=True)
class CrestMatch:
    team: EspnTeam
    score: float
    runner_up: float

    @property
    def margin(self) -> float:
        return self.score - self.runner_up


def _parse_teams(payload: dict) -> list[EspnTeam]:
    """Pull the club list out of ESPN's deeply nested envelope.

    Tolerant on purpose: this is an undocumented endpoint, so a shape change
    should cost us the clubs we cannot read rather than the whole run.
    """
    teams: list[EspnTeam] = []

    for sport in payload.get("sports", []):
        for league in sport.get("leagues", []):
            for entry in league.get("teams", []):
                team = entry.get("team") or {}
                identifier = team.get("id")
                display_name = team.get("displayName")
                if not identifier or not display_name:
                    continue

                logos = [logo.get("href") for logo in team.get("logos", []) if logo.get("href")]
                # ESPN puts the dark variant under a `500-dark/` path. Matching
                # on the path rather than on the `rel` array, because the rel
                # labels are not consistently populated.
                light = next((url for url in logos if "-dark/" not in url), None)
                dark = next((url for url in logos if "-dark/" in url), None)

                teams.append(
                    EspnTeam(
                        espn_id=str(identifier),
                        display_name=display_name,
                        short_name=team.get("shortDisplayName") or "",
                        abbreviation=team.get("abbreviation") or "",
                        logo_url=light,
                        dark_logo_url=dark,
                    )
                )

    return teams


async def fetch_espn_teams(client: httpx.AsyncClient, league_slug: str) -> list[EspnTeam]:
    """Every club ESPN lists for one league."""
    response = await client.get(ESPN_TEAMS_URL.format(slug=league_slug))
    response.raise_for_status()
    return _parse_teams(response.json())


def score_team(name: str, candidate: EspnTeam) -> float:
    """How well `name` matches any of a candidate's spellings."""
    return max((similarity(name, spelling) for spelling in candidate.names), default=0.0)


def match_team(name: str, candidates: list[EspnTeam]) -> CrestMatch | None:
    """The one club `name` clearly refers to, or None.

    None covers both "nothing resembles it" and "two things resemble it about
    equally". Those are different problems for a human to fix — one needs a
    crest from elsewhere, the other needs an alias — so `describe_failure`
    exists to tell them apart for the report.
    """
    scored = sorted(
        ((score_team(name, candidate), candidate) for candidate in candidates),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not scored:
        return None

    best_score, best_team = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0

    if best_score < MIN_CREST_SIMILARITY:
        return None
    if best_score - runner_up < MIN_CREST_MARGIN:
        log.warning(
            "Ambiguous crest for %r: %r (%.2f) vs runner-up (%.2f); leaving it unresolved",
            name,
            best_team.display_name,
            best_score,
            runner_up,
        )
        return None

    return CrestMatch(team=best_team, score=best_score, runner_up=runner_up)


def resolve_crest(
    name: str,
    primary: list[EspnTeam],
    fallback: list[EspnTeam] | None = None,
) -> CrestMatch | None:
    """Match within the club's own competition, then anywhere.

    The fallback exists because ESPN's per-league roster is not reliably the
    current one — a check of `eng.1` returned Coventry, Hull and Leeds but
    neither Wolves nor West Ham, and `ger.1` offered Schalke but not St. Pauli.
    A club missing from its own league is usually present in another list (a cup,
    or a European competition), so widening the search recovers it.

    The margin guard applies to both passes, so widening trades a miss for a
    *possible* match, never for a guess.
    """
    match = match_team(name, primary)
    if match is not None or not fallback:
        return match
    return match_team(name, fallback)


def describe_failure(name: str, candidates: list[EspnTeam]) -> str:
    """Why `match_team` refused, in a form worth printing in a report."""
    scored = sorted(
        ((score_team(name, candidate), candidate) for candidate in candidates),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not scored:
        return f"{name}: no candidates in this competition"

    best_score, best_team = scored[0]
    if best_score < MIN_CREST_SIMILARITY:
        return f"{name}: nothing close (best was {best_team.display_name!r} at {best_score:.2f})"

    runner_up_score, runner_up_team = scored[1]
    return (
        f"{name}: ambiguous between {best_team.display_name!r} ({best_score:.2f}) "
        f"and {runner_up_team.display_name!r} ({runner_up_score:.2f})"
    )


# ── Image normalisation ──────────────────────────────────────────────────────


def normalise_crest(raw: bytes, size: int) -> bytes:
    """Square WebP of `size` pixels, transparency preserved.

    Encoded losslessly when the badge fits without resampling, and lossily when
    it had to be shrunk. That is not a preference, it is what the two cases
    actually cost: untouched flat-colour art compresses losslessly to less than
    a lossy encode of the same thing, while a LANCZOS downscale introduces
    anti-aliased gradients that lossless spends enormous space on.

    Re-encoding is also the sanitiser. Whatever arrives is decoded to pixels and
    written back out, which discards EXIF, colour profiles, trailing data and
    anything polyglot — so a file that is both a valid PNG and a valid script
    cannot survive the trip.

    Pillow is imported here rather than at module scope so this file stays
    importable in the API, which does not ship it.
    """
    import io

    from PIL import Image

    with Image.open(io.BytesIO(raw)) as source:
        # `RGBA` because club badges are transparent PNGs and flattening them
        # onto white would put a white box on a near-black page.
        image = source.convert("RGBA")

        # Trim the transparent border before fitting, so clubs whose source
        # image is padded differently still render at the same visual weight.
        bounds = image.getbbox()
        if bounds:
            image = image.crop(bounds)

        # `thumbnail` only ever shrinks, so a badge smaller than the target is
        # left exactly as it is — which is the case worth detecting.
        before = image.size
        image.thumbnail((size, size), Image.LANCZOS)
        resampled = image.size != before

        # Centre on a square transparent canvas: a wide badge and a tall one
        # must both occupy the same box, or a fixture list will not line up.
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.paste(image, ((size - image.width) // 2, (size - image.height) // 2))

        if resampled:
            # A LANCZOS downscale leaves anti-aliased gradients that lossless
            # spends enormous space on, so there is nothing to weigh up.
            buffer = io.BytesIO()
            canvas.save(buffer, format="WEBP", quality=90, method=6)
            return buffer.getvalue()

        # Untouched art is usually flat colour, where lossless is both perfect
        # and smaller. Usually, not always: a badge with a photographic crest or
        # a gradient can cost several times its lossy encode. Rather than guess
        # from the artwork, encode both and keep the smaller — the comparison is
        # exact and costs a few milliseconds once, in a script.
        candidates = []
        for options in ({"lossless": True, "method": 6}, {"quality": 95, "method": 6}):
            buffer = io.BytesIO()
            canvas.save(buffer, format="WEBP", **options)
            candidates.append(buffer.getvalue())
        return min(candidates, key=len)

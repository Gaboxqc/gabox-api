"""Joining StatPitch club names to The Odds API's.

The two sources name the same club differently — StatPitch uses the full
registered name, The Odds API a short trading name:

    RCD Espanyol de Barcelona   ->  Espanyol
    Club Atlético de Madrid     ->  Atletico Madrid
    Real Betis Balompié         ->  Real Betis

Matching one name at a time is unsafe here: "RCD Espanyol de Barcelona"
normalises to `espanyol barcelona`, which resembles `barcelona` about as much
as it resembles `espanyol`. Matching the **pair** removes the ambiguity, since
the away side breaks the tie — so nothing in this module scores a team in
isolation.
"""

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

# Corporate-form and filler tokens that carry no identifying information.
# "deportivo" is deliberately absent: it is the whole name of Deportivo La
# Coruña, not a suffix.
_NOISE_TOKENS: frozenset[str] = frozenset(
    {
        "fc",
        "cf",
        "afc",
        "ac",
        "sc",
        "cd",
        "ud",
        "sd",
        "rc",
        "rcd",
        "ca",
        "as",
        "ss",
        "ssc",
        "us",
        "bsc",
        "vfb",
        "vfl",
        "tsg",
        "fsv",
        "sv",
        "spvgg",
        "bv",
        "borussia",
        # French club-form words. Without these, "Stade Brestois 29" and "Stade
        # Rennais" share a token and score 0.74 against each other, while the
        # correct "Brest" manages only 0.53 — the crest backfill matched Brest to
        # Rennes on exactly that.
        "stade",
        "olympique",
        "club",
        "calcio",
        "futbol",
        "football",
        "balompie",
        "de",
        "del",
        "della",
        "di",
        "la",
        "el",
        "les",
        "los",
        "und",
        "the",
    }
)

# Clubs whose short name shares no useful substring with the registered one, so
# no amount of token cleaning will connect them.
_ALIASES: dict[str, str] = {
    "internazionale": "inter milan",
    "inter": "inter milan",
    "milan": "ac milan",
    "spurs": "tottenham hotspur",
    "psg": "paris saint germain",
    "paris saint germain": "paris saint germain",
    "manchester united": "man united",
    "manchester city": "man city",
    "wolverhampton wanderers": "wolves",
    "brighton hove albion": "brighton and hove albion",
    "athletic bilbao": "athletic club",
    "monchengladbach": "borussia monchengladbach",
    "bayer 04 leverkusen": "bayer leverkusen",
    "1899 hoffenheim": "hoffenheim",
    "sankt pauli": "st pauli",
    # Keys are matched against the *cleaned* form, so these read as post-noise
    # tokens: "Olympique Lyonnais" arrives here as "lyonnais".
    "lyonnais": "lyon",
    "internazionale milano": "inter milan",
    # Both would otherwise tie against two different clubs — "deportivo alaves"
    # resembles Alaves and Deportivo equally, "espanyol barcelona" resembles
    # Espanyol and Barcelona equally — and a tie is refused rather than guessed.
    "deportivo alaves": "alaves",
    "espanyol barcelona": "espanyol",
}

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_DIGIT_PREFIX = re.compile(r"\b\d{1,4}\b")

# Below this, a side is treated as a non-match however good its partner looks.
MIN_SIDE_SIMILARITY = 0.55
# The pair as a whole has to clear a higher bar than either side alone.
MIN_PAIR_SIMILARITY = 0.72


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def normalize(name: str) -> str:
    """Reduce a club name to its identifying tokens.

    Order matters: aliases are applied to the cleaned form, then cleaned again,
    so an alias may be written in readable prose rather than token soup.
    """
    cleaned = _clean(name)
    aliased = _ALIASES.get(cleaned)
    return _clean(aliased) if aliased else cleaned


def _clean(name: str) -> str:
    lowered = strip_accents(name).lower()
    lowered = _PUNCTUATION.sub(" ", lowered)
    # Founding years and squad numbers: "1899 Hoffenheim", "FC Schalke 04".
    lowered = _DIGIT_PREFIX.sub(" ", lowered)
    tokens = [token for token in lowered.split() if token and token not in _NOISE_TOKENS]
    # Everything was noise ("FC de la"): fall back to the raw words so the
    # comparison has something to work with rather than an empty string.
    if not tokens:
        tokens = strip_accents(name).lower().split()
    return " ".join(tokens)


def similarity(left: str, right: str) -> float:
    """0.0-1.0 resemblance between two club names.

    Blends a character-level ratio with token overlap, then treats a full token
    subset as strong evidence — `espanyol` inside `espanyol barcelona` is the
    common shortening pattern, and the character ratio alone underrates it.
    """
    a, b = normalize(left), normalize(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    ratio = SequenceMatcher(None, a, b).ratio()

    tokens_a, tokens_b = set(a.split()), set(b.split())
    shared = tokens_a & tokens_b
    if not shared:
        return ratio

    jaccard = len(shared) / len(tokens_a | tokens_b)
    # A subset match is capped below 1.0: it is good evidence, never proof, and
    # the pair check is what decides.
    subset = 0.9 if tokens_a <= tokens_b or tokens_b <= tokens_a else 0.0
    return max(ratio, jaccard, subset)


@dataclass(frozen=True)
class PairScore:
    home: float
    away: float

    @property
    def weakest(self) -> float:
        return min(self.home, self.away)

    @property
    def mean(self) -> float:
        return (self.home + self.away) / 2

    @property
    def acceptable(self) -> bool:
        return self.weakest >= MIN_SIDE_SIMILARITY and self.mean >= MIN_PAIR_SIMILARITY


def score_pair(home: str, away: str, candidate_home: str, candidate_away: str) -> PairScore:
    return PairScore(
        home=similarity(home, candidate_home),
        away=similarity(away, candidate_away),
    )


def best_match[T](
    home: str,
    away: str,
    candidates: Iterable[T],
    key: Callable[[T], tuple[str, str]],
) -> tuple[T, PairScore] | None:
    """Pick the candidate whose (home, away) pair best matches, or None.

    Returning None is the correct outcome far more often than it looks: a
    fixture with no odds event still deserves to be stored and shown, just
    without a price. Guessing here would silently attach the wrong club's odds
    to a prediction and corrupt the ledger.
    """
    best: tuple[T, PairScore] | None = None
    for candidate in candidates:
        candidate_home, candidate_away = key(candidate)
        score = score_pair(home, away, candidate_home, candidate_away)
        if not score.acceptable:
            continue
        if best is None or score.mean > best[1].mean:
            best = (candidate, score)
    return best

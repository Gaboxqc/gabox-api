"""Final scores from ESPN.

StatPitch has no results endpoint — none of its twenty-three routes return what
a match finished, and its own ledger carries `result: null` on every row with a
CLV report that reads `"verdict": "no settled bets"`. Without an external result
source nothing can ever settle, so no ROI exists at all. This module is that
source.

It records **goal counts**, not just who won. Settling `over_2_5` or `btts_yes`
needs the scoreline, and a 1X2 outcome alone throws it away.

Why ESPN rather than The Odds API, which this used to call:

- **It needs no key and bills nothing.** The Odds API's 500-request monthly
  allowance is spent entirely on the 25-book price panel behind StatPitch's
  card. Asking it for scores as well would make prices and results compete for
  one budget, and prices are the half with no free substitute.
- **The club names already match ours.** `statpitch_team` was populated from
  ESPN during the crest backfill, and `matching._ALIASES` is tuned for ESPN's
  naming — the Köln entry exists precisely because ESPN calls them Cologne. So
  the join this feeds is against the vocabulary it was built for.
- **The league slugs already exist.** `ESPN_LEAGUE_SLUGS` was added for crests
  and the scoreboard takes the same slug, so no new mapping was needed.

The endpoint is undocumented, which is the one real cost. A failure is treated
as a warning rather than an error throughout: a run that stores fixtures and
prices but settles nothing is a partial success, and the next run picks the
results up.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx

from api.statpitch.leagues import espn_slug_for

log = logging.getLogger("statpitch.scores")

_ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

# The scoreboard accepts `dates=YYYYMMDD` or a `YYYYMMDD-YYYYMMDD` range, so one
# request covers a whole lookback per competition rather than one per day.
_DATE_FORMAT = "%Y%m%d"

# How far back to look by default. Results can arrive late and a fixture stays
# unsettled until one does, so the window is wider than a single day.
_DEFAULT_DAYS_BACK = 3


@dataclass
class MatchScore:
    competition_id: str
    home_team: str
    away_team: str
    commence_time: datetime
    completed: bool
    home_score: int | None = None
    away_score: int | None = None

    @property
    def teams(self) -> tuple[str, str]:
        return self.home_team, self.away_team

    @property
    def actual_result(self) -> str | None:
        if not self.completed or self.home_score is None or self.away_score is None:
            return None
        if self.home_score > self.away_score:
            return "home_win"
        if self.home_score < self.away_score:
            return "away_win"
        return "draw"


@dataclass
class ScoresFetch:
    scores: list[MatchScore] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    requests_used: int = 0


def _date_range(days_back: int) -> str:
    """`YYYYMMDD-YYYYMMDD`, from `days_back` ago through tomorrow.

    Tomorrow rather than today because ESPN files a fixture under its own local
    matchday, which can be a day ahead of ours for a late kickoff. Including it
    costs nothing — an unfinished match simply arrives with `completed` false.
    """
    today = datetime.now(UTC).date()
    start = today - timedelta(days=max(1, days_back))
    end = today + timedelta(days=1)
    return f"{start.strftime(_DATE_FORMAT)}-{end.strftime(_DATE_FORMAT)}"


def _parse_score(raw: object) -> int | None:
    """ESPN sends the score as a string, and omits it before kick-off."""
    if raw is None:
        return None
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return None


def _build_score(event: dict, competition_id: str) -> MatchScore | None:
    """One scoreboard event, or None if it is not usable.

    A match still in progress is returned with `completed` false rather than
    dropped: `apply_scores` filters on that flag, and keeping the row makes a
    half-finished match visible in a debug dump rather than silently absent.
    """
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    competition = competitions[0]

    sides = {
        side.get("homeAway"): side
        for side in competition.get("competitors") or []
        if side.get("homeAway") in ("home", "away")
    }
    if len(sides) != 2:
        return None

    home_team = (sides["home"].get("team") or {}).get("displayName")
    away_team = (sides["away"].get("team") or {}).get("displayName")
    raw_time = event.get("date")
    if not home_team or not away_team or not raw_time:
        return None

    try:
        commence_time = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
    except ValueError:
        return None
    if commence_time.tzinfo is None:
        commence_time = commence_time.replace(tzinfo=UTC)

    status = (competition.get("status") or {}).get("type") or {}

    return MatchScore(
        competition_id=competition_id,
        home_team=home_team,
        away_team=away_team,
        commence_time=commence_time.astimezone(UTC),
        completed=bool(status.get("completed", False)),
        home_score=_parse_score(sides["home"].get("score")),
        away_score=_parse_score(sides["away"].get("score")),
    )


async def fetch_scores(competitions: set[str], days_back: int = _DEFAULT_DAYS_BACK) -> ScoresFetch:
    """Fetch recent results for every configured competition.

    One request per competition, covering the whole lookback as a date range.
    Never raises: a competition that fails becomes a warning and the rest still
    settle, because a run that prices fixtures but cannot settle them has still
    done most of its job.
    """
    result = ScoresFetch()
    dates = _date_range(days_back)

    async with httpx.AsyncClient(timeout=30.0) as client:
        for competition_id in sorted(competitions):
            slug = espn_slug_for(competition_id)
            if slug is None:
                result.warnings.append(
                    f"{competition_id} has no ESPN slug; its fixtures cannot settle."
                )
                continue

            try:
                response = await client.get(
                    f"{_ESPN_BASE}/{slug}/scoreboard", params={"dates": dates}
                )
                response.raise_for_status()
                payload = response.json()
                result.requests_used += 1
            except Exception as exc:
                result.warnings.append(f"Could not fetch scores for {competition_id}: {exc}")
                continue

            for event in payload.get("events") or []:
                score = _build_score(event, competition_id)
                if score is not None:
                    result.scores.append(score)

    completed = sum(1 for score in result.scores if score.completed)
    log.info("Fetched %d score(s) over %s, %d completed.", len(result.scores), dates, completed)
    return result

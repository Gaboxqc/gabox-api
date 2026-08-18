"""Final scores from The Odds API.

StatPitch has no results endpoint — none of its nineteen routes return what a
match finished. Without an external result source nothing can ever settle, so
no ROI exists at all. This module is that source.

It records **goal counts**, not just who won. Settling `over_2_5` or `btts_yes`
needs the scoreline, and a 1X2 outcome alone throws it away.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from api.statpitch.leagues import sport_key_for
from api.statpitch.odds_service import OddsUnavailable, _api_key

log = logging.getLogger("statpitch.scores")

_ODDS_API_BASE = "https://api.the-odds-api.com/v4"
# The Odds API caps daysFrom at 3.
_MAX_DAYS_FROM = 3


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


def _parse_scores(event: dict, home: str, away: str) -> tuple[int | None, int | None]:
    """Pull the two goal counts out of the scores array.

    The array is keyed by team name and can arrive partially filled while a
    match is in progress, so a missing or unparseable side yields None and the
    match simply stays unsettled until the next run.
    """
    by_team: dict[str, int] = {}
    for entry in event.get("scores") or []:
        name = entry.get("name")
        raw = entry.get("score")
        if name is None or raw is None:
            continue
        try:
            by_team[str(name)] = int(raw)
        except (TypeError, ValueError):
            continue
    return by_team.get(home), by_team.get(away)


async def fetch_scores(competitions: set[str], days_back: int = 2) -> ScoresFetch:
    """Fetch recent results for every configured competition."""
    result = ScoresFetch()
    days_from = max(1, min(days_back, _MAX_DAYS_FROM))

    async with httpx.AsyncClient(timeout=30.0) as client:
        for competition_id in sorted(competitions):
            sport_key = sport_key_for(competition_id)
            if sport_key is None:
                continue

            try:
                response = await client.get(
                    f"{_ODDS_API_BASE}/sports/{sport_key}/scores/",
                    params={"apiKey": _api_key(), "daysFrom": days_from},
                )
                if response.status_code in (401, 403):
                    raise OddsUnavailable(
                        f"The Odds API rejected the API key (HTTP {response.status_code})."
                    )
                if response.status_code == 429:
                    raise OddsUnavailable("The Odds API request quota is exhausted (HTTP 429).")
                response.raise_for_status()
                result.requests_used += 1
            except OddsUnavailable:
                raise
            except Exception as exc:
                result.warnings.append(f"Could not fetch scores for {competition_id}: {exc}")
                continue

            for event in response.json():
                score = _build_score(event, competition_id)
                if score is not None:
                    result.scores.append(score)

    completed = sum(1 for score in result.scores if score.completed)
    log.info("Fetched %d score(s), %d completed.", len(result.scores), completed)
    return result


def _build_score(event: dict, competition_id: str) -> MatchScore | None:
    home = event.get("home_team")
    away = event.get("away_team")
    raw_time = event.get("commence_time")
    if not home or not away or not raw_time:
        return None

    try:
        commence_time = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
    except ValueError:
        return None
    if commence_time.tzinfo is None:
        commence_time = commence_time.replace(tzinfo=UTC)

    home_score, away_score = _parse_scores(event, home, away)

    return MatchScore(
        competition_id=competition_id,
        home_team=home,
        away_team=away,
        commence_time=commence_time.astimezone(UTC),
        completed=bool(event.get("completed", False)),
        home_score=home_score,
        away_score=away_score,
    )

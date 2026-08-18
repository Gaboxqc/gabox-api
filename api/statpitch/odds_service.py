"""Real bookmaker prices from The Odds API.

StatPitch supplies probabilities but no bettable price — its `fair_odds` is
`1 / probability`, a no-vig number it explicitly says you cannot bet. Without a
real price there is no expected value, no stake and no ROI, so this module is
what makes the whole track record possible.

It is also the quota bottleneck. One request per market per league per run,
against a 500/month free tier, is why `odds_api_markets` defaults to `h2h`
alone: five leagues once a day is ~150 requests/month, while adding totals and
btts takes it past 450 before scores are counted.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from api.core.config import settings
from api.statpitch.leagues import sport_key_for

log = logging.getLogger("statpitch.odds")

_ODDS_API_BASE = "https://api.the-odds-api.com/v4"
# btts is a UK-book market; asking for it in the EU region returns nothing.
_BTTS_REGION = "uk"


class OddsUnavailable(RuntimeError):
    """The Odds API is not configured, or refused the credentials."""


@dataclass
class OddsEvent:
    """One event with whatever markets we successfully priced."""

    event_id: str
    competition_id: str
    home_team: str
    away_team: str
    # A real UTC instant, unlike StatPitch's naive `kickoff` string. This is
    # what every local-day bucket is computed from.
    commence_time: datetime

    odds_home: float | None = None
    odds_draw: float | None = None
    odds_away: float | None = None
    odds_over_1_5: float | None = None
    odds_under_1_5: float | None = None
    odds_over_2_5: float | None = None
    odds_under_2_5: float | None = None
    odds_over_3_5: float | None = None
    odds_under_3_5: float | None = None
    odds_btts_yes: float | None = None
    odds_btts_no: float | None = None

    @property
    def teams(self) -> tuple[str, str]:
        return self.home_team, self.away_team


@dataclass
class OddsFetch:
    events: list[OddsEvent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    requests_used: int = 0
    requests_remaining: int | None = None


def _api_key() -> str:
    key = settings.odds_api_key.strip()
    if not key:
        raise OddsUnavailable("ODDS_API_KEY is not configured on the server.")
    return key


def _preferred_bookmakers() -> set[str]:
    return {book.strip() for book in settings.odds_api_bookmakers if book.strip()}


def _iter_markets(bookmakers: list[dict], wanted: str):
    """Yield outcomes of one market across every bookmaker we accept."""
    preferred = _preferred_bookmakers()
    for bookmaker in bookmakers:
        if preferred and bookmaker.get("key") not in preferred:
            continue
        for market in bookmaker.get("markets", []):
            if market.get("key") != wanted:
                continue
            yield from market.get("outcomes", [])


def _average(prices: list[float]) -> float | None:
    """Mean price across books. None when nothing quoted — never 0.0, which
    would read downstream as a real price of zero."""
    return round(sum(prices) / len(prices), 3) if prices else None


def _parse_h2h(bookmakers: list[dict], home: str, away: str) -> tuple[list[float], ...]:
    home_prices: list[float] = []
    draw_prices: list[float] = []
    away_prices: list[float] = []

    for outcome in _iter_markets(bookmakers, "h2h"):
        name = (outcome.get("name") or "").strip()
        price = outcome.get("price")
        if not isinstance(price, (int, float)) or price <= 1:
            continue
        # Both sides of this comparison come from The Odds API, so the names
        # are identical by construction — no normalisation needed here.
        if name == home:
            home_prices.append(float(price))
        elif name == away:
            away_prices.append(float(price))
        elif name.lower() == "draw":
            draw_prices.append(float(price))

    return home_prices, draw_prices, away_prices


def _parse_totals(bookmakers: list[dict]) -> dict[str, float | None]:
    buckets: dict[str, list[float]] = {
        "over_1_5": [],
        "under_1_5": [],
        "over_2_5": [],
        "under_2_5": [],
        "over_3_5": [],
        "under_3_5": [],
    }

    for outcome in _iter_markets(bookmakers, "totals"):
        name = (outcome.get("name") or "").lower()
        point = outcome.get("point")
        price = outcome.get("price")
        if point not in (1.5, 2.5, 3.5) or name not in ("over", "under"):
            continue
        if not isinstance(price, (int, float)) or price <= 1:
            continue
        buckets[f"{name}_{str(point).replace('.', '_')}"].append(float(price))

    return {key: _average(prices) for key, prices in buckets.items()}


def _parse_btts(bookmakers: list[dict]) -> tuple[float | None, float | None]:
    yes_prices: list[float] = []
    no_prices: list[float] = []

    for outcome in _iter_markets(bookmakers, "btts"):
        name = (outcome.get("name") or "").lower()
        price = outcome.get("price")
        if not isinstance(price, (int, float)) or price <= 1:
            continue
        if name == "yes":
            yes_prices.append(float(price))
        elif name == "no":
            no_prices.append(float(price))

    return _average(yes_prices), _average(no_prices)


def _merge_bookmakers(target: dict, incoming: dict) -> None:
    """Fold one market's response into an event already holding another.

    Each market is a separate request, so an event accumulates its markets
    across calls. Splitting them this way means one unsupported market cannot
    take the others down with it.
    """
    existing = {book["key"]: book for book in target.get("bookmakers", [])}
    for book in incoming.get("bookmakers", []):
        key = book["key"]
        if key in existing:
            existing[key].setdefault("markets", []).extend(book.get("markets", []))
        else:
            existing[key] = book
    target["bookmakers"] = list(existing.values())


async def _fetch_market(
    client: httpx.AsyncClient, sport_key: str, market: str
) -> tuple[list[dict], int | None]:
    region = _BTTS_REGION if market == "btts" else settings.odds_api_region
    response = await client.get(
        f"{_ODDS_API_BASE}/sports/{sport_key}/odds/",
        params={
            "apiKey": _api_key(),
            "regions": region,
            "markets": market,
            "oddsFormat": "decimal",
        },
    )
    if response.status_code in (401, 403):
        raise OddsUnavailable(f"The Odds API rejected the API key (HTTP {response.status_code}).")
    if response.status_code == 429:
        raise OddsUnavailable("The Odds API request quota is exhausted (HTTP 429).")
    response.raise_for_status()

    remaining = response.headers.get("x-requests-remaining")
    return response.json(), int(remaining) if remaining and remaining.isdigit() else None


async def fetch_odds(competitions: set[str]) -> OddsFetch:
    """Fetch prices for every configured competition that has a sport key."""
    result = OddsFetch()
    markets = [market for market in settings.odds_api_markets if market.strip()]
    if not markets:
        result.warnings.append("No odds markets configured; fixtures will be stored unpriced.")
        return result

    async with httpx.AsyncClient(timeout=30.0) as client:
        for competition_id in sorted(competitions):
            sport_key = sport_key_for(competition_id)
            if sport_key is None:
                result.warnings.append(
                    f"{competition_id} has no Odds API sport key; its fixtures stay unpriced."
                )
                continue

            events_by_id: dict[str, dict] = {}

            for market in markets:
                try:
                    payload, remaining = await _fetch_market(client, sport_key, market)
                    result.requests_used += 1
                    if remaining is not None:
                        result.requests_remaining = remaining
                except OddsUnavailable:
                    # Key or quota problems affect every league, so stop rather
                    # than burning the remainder of the budget on failures.
                    raise
                except Exception as exc:
                    result.warnings.append(
                        f"Could not fetch '{market}' odds for {competition_id}: {exc}"
                    )
                    continue

                for event in payload:
                    event_id = event.get("id")
                    if not event_id:
                        continue
                    if event_id in events_by_id:
                        _merge_bookmakers(events_by_id[event_id], event)
                    else:
                        events_by_id[event_id] = event

            for event in events_by_id.values():
                parsed = _build_event(event, competition_id)
                if parsed is not None:
                    result.events.append(parsed)

            log.info(
                "Fetched %d priced event(s) for %s.",
                sum(1 for e in result.events if e.competition_id == competition_id),
                competition_id,
            )

    if result.requests_remaining is not None:
        log.info("The Odds API quota remaining: %s", result.requests_remaining)

    return result


def _build_event(event: dict, competition_id: str) -> OddsEvent | None:
    raw_time = event.get("commence_time")
    home = event.get("home_team")
    away = event.get("away_team")
    if not raw_time or not home or not away:
        return None

    try:
        commence_time = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
    except ValueError:
        return None
    if commence_time.tzinfo is None:
        commence_time = commence_time.replace(tzinfo=UTC)

    bookmakers = event.get("bookmakers", [])
    home_prices, draw_prices, away_prices = _parse_h2h(bookmakers, home, away)
    totals = _parse_totals(bookmakers)
    btts_yes, btts_no = _parse_btts(bookmakers)

    return OddsEvent(
        event_id=str(event["id"]),
        competition_id=competition_id,
        home_team=home,
        away_team=away,
        commence_time=commence_time.astimezone(UTC),
        odds_home=_average(home_prices),
        odds_draw=_average(draw_prices),
        odds_away=_average(away_prices),
        odds_over_1_5=totals["over_1_5"],
        odds_under_1_5=totals["under_1_5"],
        odds_over_2_5=totals["over_2_5"],
        odds_under_2_5=totals["under_2_5"],
        odds_over_3_5=totals["over_3_5"],
        odds_under_3_5=totals["under_3_5"],
        odds_btts_yes=btts_yes,
        odds_btts_no=btts_no,
    )

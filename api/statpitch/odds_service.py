import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException, status

log = logging.getLogger("statpitch.odds_service")

_ODDS_API_BASE = "https://api.the-odds-api.com/v4"
_DEFAULT_SPORT = "soccer_fifa_world_cup"
_DEFAULT_REGION = "eu"


@dataclass
class MatchOdds:
    """Parsed, bookmaker-averaged odds for a single match across all markets."""

    home_team: str
    away_team: str
    match_date: date
    commence_time: Optional[datetime] = None

    # 1X2
    odds_home: Optional[float] = None
    odds_draw: Optional[float] = None
    odds_away: Optional[float] = None

    # Over/Under
    odds_over_1_5: Optional[float] = None
    odds_under_1_5: Optional[float] = None
    odds_over_2_5: Optional[float] = None
    odds_under_2_5: Optional[float] = None
    odds_over_3_5: Optional[float] = None
    odds_under_3_5: Optional[float] = None

    # BTTS
    odds_btts_yes: Optional[float] = None
    odds_btts_no: Optional[float] = None

    # Flags
    home_flag_url: Optional[str] = None
    away_flag_url: Optional[str] = None


_TEAM_NAME_MAP: dict[str, str] = {
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Türkiye": "Turkey",
    "Korea Republic": "South Korea",
    "IR Iran": "Iran",
    "USA": "United States",
    "Côte d'Ivoire": "Ivory Coast",
    "Cabo Verde": "Cape Verde",
    "Congo DR": "DR Congo",
}


def _normalize_team_name(name: str) -> str:
    return _TEAM_NAME_MAP.get(name, name)


def _team_to_flag_url(team_name: str) -> str:
    FLAG_MAP: dict[str, str] = {
        # ── CONMEBOL (6) ──────────────────────────────────────────────────────
        "argentina": "ar",
        "brazil": "br",
        "colombia": "co",
        "ecuador": "ec",
        "paraguay": "py",
        "uruguay": "uy",
        # ── CONCACAF (6) ─────────────────────────────────────────────────────
        "canada": "ca",
        "mexico": "mx",
        "united states": "us",
        "usa": "us",
        "curaçao": "cw",
        "curacao": "cw",
        "haiti": "ht",
        "panama": "pa",
        # ── UEFA (16) ─────────────────────────────────────────────────────────
        "austria": "at",
        "belgium": "be",
        "bosnia and herzegovina": "ba",
        "bosnia & herzegovina": "ba",
        "croatia": "hr",
        "czechia": "cz",
        "czech republic": "cz",
        "england": "gb-eng",
        "france": "fr",
        "germany": "de",
        "netherlands": "nl",
        "norway": "no",
        "portugal": "pt",
        "scotland": "gb-sct",
        "spain": "es",
        "sweden": "se",
        "switzerland": "ch",
        "türkiye": "tr",
        "turkey": "tr",
        # ── AFC (9) ───────────────────────────────────────────────────────────
        "australia": "au",
        "iraq": "iq",
        "iran": "ir",
        "ir iran": "ir",
        "japan": "jp",
        "jordan": "jo",
        "south korea": "kr",
        "korea republic": "kr",
        "qatar": "qa",
        "saudi arabia": "sa",
        "uzbekistan": "uz",
        # ── CAF (10) ──────────────────────────────────────────────────────────
        "algeria": "dz",
        "cabo verde": "cv",
        "cape verde": "cv",
        "dr congo": "cd",
        "congo dr": "cd",
        "ivory coast": "ci",
        "côte d'ivoire": "ci",
        "cote d'ivoire": "ci",
        "egypt": "eg",
        "ghana": "gh",
        "morocco": "ma",
        "senegal": "sn",
        "south africa": "za",
        "tunisia": "tn",
        # ── OFC (1) ───────────────────────────────────────────────────────────
        "new zealand": "nz",
    }
    code = FLAG_MAP.get(team_name.lower())
    return f"https://flagcdn.com/{code}.svg" if code else ""


def _preferred_bookmakers() -> set[str]:
    raw = os.getenv("ODDS_API_BOOKMAKERS", "")
    return {k.strip() for k in raw.split(",") if k.strip()}


def _parse_h2h(
    bookmakers: list[dict], home_team: str, away_team: str
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    home_p, draw_p, away_p = [], [], []
    preferred = _preferred_bookmakers()

    for bm in bookmakers:
        if preferred and bm.get("key") not in preferred:
            continue
        for market in bm.get("markets", []):
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes", []):
                name = _normalize_team_name(outcome.get("name", "")).lower()
                price = outcome.get("price", 0.0)
                if name == home_team.lower():
                    home_p.append(price)
                elif name == away_team.lower():
                    away_p.append(price)
                elif name == "draw":
                    draw_p.append(price)

    if not home_p or not away_p or not draw_p:
        return None, None, None

    return (
        round(sum(home_p) / len(home_p), 3),
        round(sum(draw_p) / len(draw_p), 3),
        round(sum(away_p) / len(away_p), 3),
    )


def _parse_totals(bookmakers: list[dict]) -> dict[str, Optional[float]]:
    buckets: dict[str, list[float]] = {
        "over_1_5": [],
        "under_1_5": [],
        "over_2_5": [],
        "under_2_5": [],
        "over_3_5": [],
        "under_3_5": [],
    }
    preferred = _preferred_bookmakers()

    for bm in bookmakers:
        if preferred and bm.get("key") not in preferred:
            continue
        for market in bm.get("markets", []):
            if market.get("key") != "totals":
                continue
            for outcome in market.get("outcomes", []):
                name = outcome.get("name", "").lower()
                point = outcome.get("point")
                price = outcome.get("price", 0.0)
                if point not in (1.5, 2.5, 3.5):
                    continue
                key = f"{name}_{str(point).replace('.', '_')}"
                if key in buckets:
                    buckets[key].append(price)

    return {k: round(sum(v) / len(v), 3) if v else None for k, v in buckets.items()}


def _parse_btts(bookmakers: list[dict]) -> tuple[Optional[float], Optional[float]]:
    yes_p, no_p = [], []
    preferred = _preferred_bookmakers()

    for bm in bookmakers:
        if preferred and bm.get("key") not in preferred:
            continue
        for market in bm.get("markets", []):
            if market.get("key") != "btts":
                continue
            for outcome in market.get("outcomes", []):
                name = outcome.get("name", "").lower()
                price = outcome.get("price", 0.0)
                if name == "yes":
                    yes_p.append(price)
                elif name == "no":
                    no_p.append(price)

    yes = round(sum(yes_p) / len(yes_p), 3) if yes_p else None
    no = round(sum(no_p) / len(no_p), 3) if no_p else None
    return yes, no


def _merge_bookmakers(existing: dict, new_event: dict) -> None:
    """
    Merge bookmaker market data from new_event into existing in-place.
    Each market (h2h, totals, btts) comes from a separate API call —
    this combines them so all three are available on a single event dict.
    """
    existing_bms = {bm["key"]: bm for bm in existing.get("bookmakers", [])}

    for bm in new_event.get("bookmakers", []):
        key = bm["key"]
        if key in existing_bms:
            existing_bms[key]["markets"].extend(bm.get("markets", []))
        else:
            existing_bms[key] = bm

    existing["bookmakers"] = list(existing_bms.values())


async def fetch_todays_odds() -> list[MatchOdds]:
    api_key = os.getenv("ODDS_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ODDS_API_KEY is not configured on the server.",
        )

    sport = os.getenv("ODDS_API_SPORT", _DEFAULT_SPORT)
    region = os.getenv("ODDS_API_REGION", _DEFAULT_REGION)

    target_tz = ZoneInfo("America/Managua")
    today_local = datetime.now(target_tz).date()

    async with httpx.AsyncClient(timeout=30.0) as client:

        # ── Step 1: all fixtures for today (no odds yet, just schedule) ───────
        try:
            events_resp = await client.get(
                f"{_ODDS_API_BASE}/sports/{sport}/events/",
                params={"apiKey": api_key},
            )
            events_resp.raise_for_status()
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="The Odds API timed out.")
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"The Odds API returned HTTP {exc.response.status_code}.",
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Could not reach The Odds API: {exc}")

        # ── Step 2: fetch each market separately ──────────────────────────────
        # Splitting into three calls means one unsupported market (e.g. btts)
        # won't silently kill the entire odds response.
        odds_by_id: dict[str, dict] = {}

        for market in ["h2h", "totals", "btts"]:
            region_for_market = "uk" if market == "btts" else region
            try:
                resp = await client.get(
                    f"{_ODDS_API_BASE}/sports/{sport}/odds/",
                    params={
                        "apiKey": api_key,
                        "regions": region_for_market,
                        "markets": market,
                        "oddsFormat": "decimal",
                    },
                )
                resp.raise_for_status()
                for event in resp.json():
                    event_id = event["id"]
                    if event_id not in odds_by_id:
                        odds_by_id[event_id] = event
                    else:
                        _merge_bookmakers(odds_by_id[event_id], event)
                log.info(f"✅ {market} odds fetched for {len(resp.json())} events.")
            except Exception as e:
                # Log and continue — other markets are still usable
                log.warning(f"⚠️  Could not fetch '{market}' odds: {e}")

    # ── Step 3: build MatchOdds for each event scheduled today ───────────────
    results: list[MatchOdds] = []

    for event in events_resp.json():
        commence_time = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
        match_date_local = commence_time.astimezone(target_tz).date()
        match_time_local = commence_time.astimezone(target_tz)

        if match_date_local != today_local:
            continue

        home_team = _normalize_team_name(event["home_team"])
        away_team = _normalize_team_name(event["away_team"])

        bookmakers = odds_by_id.get(event["id"], {}).get("bookmakers", [])

        odds_home, odds_draw, odds_away = _parse_h2h(bookmakers, home_team, away_team)
        totals = _parse_totals(bookmakers)
        btts_yes, btts_no = _parse_btts(bookmakers)

        results.append(
            MatchOdds(
                home_team=home_team,
                away_team=away_team,
                match_date=today_local,
                commence_time=match_time_local,
                odds_home=odds_home,
                odds_draw=odds_draw,
                odds_away=odds_away,
                odds_over_1_5=totals["over_1_5"],
                odds_under_1_5=totals["under_1_5"],
                odds_over_2_5=totals["over_2_5"],
                odds_under_2_5=totals["under_2_5"],
                odds_over_3_5=totals["over_3_5"],
                odds_under_3_5=totals["under_3_5"],
                odds_btts_yes=btts_yes,
                odds_btts_no=btts_no,
                home_flag_url=_team_to_flag_url(home_team),
                away_flag_url=_team_to_flag_url(away_team),
            )
        )

    return results

import asyncio
import os
from datetime import date, timedelta
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import select

from api.database import SessionDep
from api.security import validate_api_key
from api.statpitch.models import (
    DailyStatsRead,
    MatchPrediction,
    MatchPredictionCreate,
    MatchPredictionRead,
    MatchResultUpdate,
    MLPredictionResponse,
    SyncResultRead,
)
from api.statpitch.odds_service import MatchOdds, fetch_todays_odds
from api.statpitch.scheduler import check_and_record_results, sync_tomorrow

router = APIRouter()

_ML_TIMEOUT = 90.0
HIGH_CONFIDENCE_THRESHOLD = 0.70

# ── Kelly settings ────────────────────────────────────────────────────────────
# Full Kelly can be too aggressive. 1/4 Kelly is the standard conservative
# approach — same optimal pick, just 25% of the theoretically ideal stake.
KELLY_FRACTION = 0.25

# Minimum FRACTIONAL Kelly to consider a bet worth placing.
# Below this the edge exists but the variance is too high for the probability.
# 0.02 means "at least 2% of bankroll recommended" after the fraction reduction.
MIN_KELLY = 0.02


# ==============================================================================
# MATH HELPERS
# ==============================================================================


def _ev(prob: float, odds: float) -> float:
    """Expected Value: how much you gain per unit staked on average."""
    return round((prob * odds) - 1, 4)


def _kelly(prob: float, odds: float) -> float:
    """
    Full Kelly fraction — the mathematically optimal % of bankroll to bet.

    Kelly = (probability × odds - 1) / (odds - 1)

    Negative → no edge, skip.
    Naturally accounts for BOTH probability and odds together, solving the
    "high EV but low probability" problem: a 5% chance at 20x odds gives
    EV=0% and Kelly=0 (break-even), while a 60% chance at 2x odds gives
    EV=+20% and Kelly=20% (strong bet).
    """
    b = odds - 1  # net profit per unit staked
    return round((prob * odds - 1) / b, 4)


def _fractional_kelly(prob: float, odds: float) -> Optional[float]:
    """
    Returns KELLY_FRACTION × full Kelly if above MIN_KELLY, else None.
    None means "don't bet" — either negative edge or too low to be worth it.
    """
    k = _kelly(prob, odds)
    fk = round(k * KELLY_FRACTION, 4)
    return fk if fk >= MIN_KELLY else None


# ==============================================================================
# EV + KELLY COMPUTATION
# ==============================================================================


def _compute_ev(p: MatchPrediction) -> None:
    """
    Compute EV and Kelly for every available market, then set best_overall_bet
    to the highest fractional-Kelly pick that passes MIN_KELLY.

    Candidates dict: { bet_name: (ev, fractional_kelly) }
    """
    candidates: dict[str, tuple[float, float]] = {}

    # ── 1X2 ──────────────────────────────────────────────────────────────────
    if all([p.odds_home, p.odds_draw, p.odds_away]):
        market_1x2 = [
            ("home_win", p.home_win_prob, p.odds_home, "ev_home", "kelly_home"),
            ("draw", p.draw_prob, p.odds_draw, "ev_draw", "kelly_draw"),
            ("away_win", p.away_win_prob, p.odds_away, "ev_away", "kelly_away"),
        ]
        best_1x2_name: Optional[str] = None
        best_1x2_fk: float = -1

        for name, prob, odds, ev_attr, kelly_attr in market_1x2:
            ev = _ev(prob, odds)
            fk = _fractional_kelly(prob, odds)
            setattr(p, ev_attr, ev)
            setattr(p, kelly_attr, fk)
            if fk is not None:
                candidates[name] = (ev, fk)
                if fk > best_1x2_fk:
                    best_1x2_fk = fk
                    best_1x2_name = name

        p.best_bet = best_1x2_name
    else:
        p.ev_home = p.ev_draw = p.ev_away = None
        p.kelly_home = p.kelly_draw = p.kelly_away = None
        p.best_bet = None

    # ── Over/Under ────────────────────────────────────────────────────────────
    ou_markets = [
        ("over_1_5", "under_1_5", p.over_1_5, p.odds_over_1_5, p.odds_under_1_5),
        ("over_2_5", "under_2_5", p.over_2_5, p.odds_over_2_5, p.odds_under_2_5),
        ("over_3_5", "under_3_5", p.over_3_5, p.odds_over_3_5, p.odds_under_3_5),
    ]
    for over_key, under_key, prob_over, odds_over, odds_under in ou_markets:
        prob_under = 1 - prob_over  # ML gives P(over), P(under) = complement

        if odds_over:
            ev = _ev(prob_over, odds_over)
            fk = _fractional_kelly(prob_over, odds_over)
            setattr(p, f"ev_{over_key}", ev)
            setattr(p, f"kelly_{over_key}", fk)
            if fk is not None:
                candidates[over_key] = (ev, fk)
        else:
            setattr(p, f"ev_{over_key}", None)
            setattr(p, f"kelly_{over_key}", None)

        if odds_under:
            ev = _ev(prob_under, odds_under)
            fk = _fractional_kelly(prob_under, odds_under)
            setattr(p, f"ev_{under_key}", ev)
            setattr(p, f"kelly_{under_key}", fk)
            if fk is not None:
                candidates[under_key] = (ev, fk)
        else:
            setattr(p, f"ev_{under_key}", None)
            setattr(p, f"kelly_{under_key}", None)

    # ── BTTS ─────────────────────────────────────────────────────────────────
    btts_markets = [
        ("btts_yes", p.btts_yes, p.odds_btts_yes, "ev_btts_yes", "kelly_btts_yes"),
        ("btts_no", p.btts_no, p.odds_btts_no, "ev_btts_no", "kelly_btts_no"),
    ]
    for name, prob, odds, ev_attr, kelly_attr in btts_markets:
        if odds:
            ev = _ev(prob, odds)
            fk = _fractional_kelly(prob, odds)
            setattr(p, ev_attr, ev)
            setattr(p, kelly_attr, fk)
            if fk is not None:
                candidates[name] = (ev, fk)
        else:
            setattr(p, ev_attr, None)
            setattr(p, kelly_attr, None)

    # ── Best overall ──────────────────────────────────────────────────────────
    # Winner = highest fractional Kelly across all markets.
    # This naturally filters out high-EV / low-probability picks:
    # they will have small Kelly and won't beat a confident moderate-EV pick.
    if candidates:
        best = max(candidates, key=lambda k: candidates[k][1])
        p.best_overall_bet = best
        p.best_overall_ev = candidates[best][0]
        p.best_overall_kelly = candidates[best][1]
    else:
        p.best_overall_bet = None
        p.best_overall_ev = None
        p.best_overall_kelly = None


def _predicted_outcome(p: MatchPrediction) -> str:
    return max(
        {"home_win": p.home_win_prob, "draw": p.draw_prob, "away_win": p.away_win_prob},
        key=lambda k: {
            "home_win": p.home_win_prob,
            "draw": p.draw_prob,
            "away_win": p.away_win_prob,
        }[k],
    )


def _ml_to_db(
    ml: MLPredictionResponse,
    target_date: date,
    is_neutral: bool,
    odds: MatchOdds,
) -> MatchPrediction:
    prediction = MatchPrediction(
        match_date=target_date,
        home_team=ml.home_team,
        away_team=ml.away_team,
        is_neutral=is_neutral,
        home_flag_url=odds.home_flag_url,
        away_flag_url=odds.away_flag_url,
        model_version=ml.model_version,
        home_xg=ml.expected_goals.home,
        away_xg=ml.expected_goals.away,
        home_elo=ml.team_info.home_elo,
        away_elo=ml.team_info.away_elo,
        elo_diff=ml.team_info.elo_diff,
        h2h_games=ml.team_info.h2h_games,
        h2h_home_wins=ml.team_info.h2h_home_wins,
        home_win_prob=ml.match_result.home_win,
        draw_prob=ml.match_result.draw,
        away_win_prob=ml.match_result.away_win,
        over_1_5=ml.over_under.over_1_5,
        over_2_5=ml.over_under.over_2_5,
        over_3_5=ml.over_under.over_3_5,
        btts_yes=ml.btts.yes,
        btts_no=ml.btts.no,
        # 1X2
        odds_home=odds.odds_home,
        odds_draw=odds.odds_draw,
        odds_away=odds.odds_away,
        # Over/Under
        odds_over_1_5=odds.odds_over_1_5,
        odds_under_1_5=odds.odds_under_1_5,
        odds_over_2_5=odds.odds_over_2_5,
        odds_under_2_5=odds.odds_under_2_5,
        odds_over_3_5=odds.odds_over_3_5,
        odds_under_3_5=odds.odds_under_3_5,
        # BTTS
        odds_btts_yes=odds.odds_btts_yes,
        odds_btts_no=odds.odds_btts_no,
    )
    _compute_ev(prediction)
    return prediction


# ==============================================================================
# HTTP HELPERS
# ==============================================================================


async def _fetch_ml_one(
    client: httpx.AsyncClient,
    home_team: str,
    away_team: str,
    is_neutral: bool = True,
) -> MLPredictionResponse:
    try:
        response = await client.get(
            f"{_get_ml_url()}/{home_team}/{away_team}",
            params={"is_neutral": is_neutral},
        )
        response.raise_for_status()
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"ML model timed out on {home_team} vs {away_team}. Wait 60 s and retry.",
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"ML model returned HTTP {exc.response.status_code} for {home_team} vs {away_team}.",
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach ML model: {exc}",
        )
    return MLPredictionResponse.model_validate(response.json())


def _get_ml_url() -> str:
    url = os.getenv("STATPITCH_ML_URL", "").rstrip("/")
    if not url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="STATPITCH_ML_URL is not configured on the server.",
        )
    return url


def _get_existing(
    db: SessionDep, target_date: date, home_team: str, away_team: str
) -> Optional[MatchPrediction]:
    return db.exec(
        select(MatchPrediction).where(
            MatchPrediction.match_date == target_date,
            MatchPrediction.home_team == home_team,
            MatchPrediction.away_team == away_team,
        )
    ).first()


def _upsert(
    db: SessionDep, prediction: MatchPrediction, existing: Optional[MatchPrediction]
) -> MatchPrediction:
    if existing:
        for field, value in prediction.model_dump(exclude={"id"}).items():
            setattr(existing, field, value)
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing
    db.add(prediction)
    db.commit()
    db.refresh(prediction)
    return prediction


# ==============================================================================
# WRITE ENDPOINTS
# ==============================================================================


@router.post(
    "/predictions/sync",
    response_model=SyncResultRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(validate_api_key)],
    summary="Full daily sync — fetches matches, odds, ML predictions, computes EV and Kelly",
)
async def sync_predictions(
    db: SessionDep,
    force: bool = Query(False, description="Re-fetch and overwrite already-cached matches."),
    is_neutral: bool = Query(True, description="Neutral venue (always true for World Cup)."),
):
    today = date.today()
    todays_odds: list[MatchOdds] = await fetch_todays_odds()

    if not todays_odds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The Odds API returned no matches for today. Rest day or wrong sport key.",
        )

    to_fetch: list[tuple[MatchOdds, Optional[MatchPrediction]]] = []
    skipped: list[MatchPrediction] = []

    for odds in todays_odds:
        existing = _get_existing(db, today, odds.home_team, odds.away_team)
        if existing and not force:
            skipped.append(existing)
        else:
            to_fetch.append((odds, existing))

    synced: list[MatchPrediction] = []

    if to_fetch:
        async with httpx.AsyncClient(timeout=_ML_TIMEOUT) as client:
            ml_responses = await asyncio.gather(
                *[_fetch_ml_one(client, o.home_team, o.away_team, is_neutral) for o, _ in to_fetch]
            )
        for (odds, existing), ml_data in zip(to_fetch, ml_responses):
            synced.append(_upsert(db, _ml_to_db(ml_data, today, is_neutral, odds), existing))

    return SyncResultRead(
        synced=len(synced), skipped=len(skipped), date=today, matches=synced + skipped
    )


@router.post(
    "/predictions",
    response_model=MatchPredictionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(validate_api_key)],
    summary="Manually fetch a single match prediction",
)
async def create_prediction(
    payload: MatchPredictionCreate,
    db: SessionDep,
    force: bool = Query(False),
):
    target_date = payload.match_date or date.today()
    existing = _get_existing(db, target_date, payload.home_team, payload.away_team)
    if existing and not force:
        return existing

    async with httpx.AsyncClient(timeout=_ML_TIMEOUT) as client:
        ml_data = await _fetch_ml_one(
            client, payload.home_team, payload.away_team, payload.is_neutral
        )

    manual_odds = MatchOdds(
        home_team=payload.home_team,
        away_team=payload.away_team,
        match_date=target_date,
        odds_home=payload.odds_home,
        odds_draw=payload.odds_draw,
        odds_away=payload.odds_away,
        home_flag_url=payload.home_flag_url,
        away_flag_url=payload.away_flag_url,
    )
    return _upsert(db, _ml_to_db(ml_data, target_date, payload.is_neutral, manual_odds), existing)


@router.patch(
    "/predictions/{prediction_id}/result",
    response_model=MatchPredictionRead,
    dependencies=[Depends(validate_api_key)],
    summary="Record the actual result after a match ends",
)
async def record_match_result(prediction_id: int, payload: MatchResultUpdate, db: SessionDep):
    prediction = db.get(MatchPrediction, prediction_id)
    if not prediction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Prediction {prediction_id} not found"
        )
    prediction.actual_result = payload.actual_result
    db.add(prediction)
    db.commit()
    db.refresh(prediction)
    return prediction


@router.delete(
    "/predictions/{prediction_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(validate_api_key)],
)
async def delete_prediction(prediction_id: int, db: SessionDep):
    prediction = db.get(MatchPrediction, prediction_id)
    if not prediction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Prediction {prediction_id} not found"
        )
    db.delete(prediction)
    db.commit()
    return None


@router.post(
    "/predictions/check-results",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(validate_api_key)],
    summary="Manually trigger result checking (same as the hourly job)",
    description=(
        "Fetches today's scores from The Odds API, marks completed matches, "
        "and triggers tomorrow's sync if all matches are resolved. "
        "Use this to test the automation or force a result check."
    ),
)
async def trigger_check_results():
    await check_and_record_results()
    return {"detail": "Result check completed. Check server logs for details."}


@router.post(
    "/predictions/sync-tomorrow",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(validate_api_key)],
    summary="Manually trigger tomorrow's match sync",
    description="Fetches tomorrow's matches, odds, and ML predictions. Same as the 02:00 UTC daily job.",
)
async def trigger_sync_tomorrow():
    await sync_tomorrow()
    return {"detail": "Tomorrow's sync completed. Check server logs for details."}


# ==============================================================================
# READ ENDPOINTS
# ==============================================================================


@router.get(
    "/predictions/stats",
    response_model=DailyStatsRead,
    summary="Stats bar — predictions today, high confidence, value bets, 30d accuracy, 30d ROI",
)
async def get_stats(db: SessionDep):
    today = date.today()
    cutoff = today - timedelta(days=30)

    today_preds = db.exec(select(MatchPrediction).where(MatchPrediction.match_date == today)).all()

    settled = db.exec(
        select(MatchPrediction).where(
            MatchPrediction.match_date >= cutoff,
            MatchPrediction.match_date < today,
            MatchPrediction.actual_result.is_not(None),
        )
    ).all()

    settled_count = len(settled)

    accuracy_30d: Optional[float] = None
    if settled_count:
        correct = sum(1 for p in settled if _predicted_outcome(p) == p.actual_result)
        accuracy_30d = round(correct / settled_count * 100, 1)

    roi_30d: Optional[float] = None
    odds_map = {"home_win": "odds_home", "draw": "odds_draw", "away_win": "odds_away"}
    settled_with_odds = [
        p for p in settled if p.best_bet and getattr(p, odds_map.get(p.best_bet, ""), None)
    ]
    if settled_with_odds:
        total_staked = len(settled_with_odds)
        total_returns = sum(
            getattr(p, odds_map[p.best_bet])
            for p in settled_with_odds
            if p.actual_result == p.best_bet
        )
        roi_30d = round((total_returns - total_staked) / total_staked * 100, 1)

    return DailyStatsRead(
        predictions_today=len(today_preds),
        high_confidence_today=sum(
            1
            for p in today_preds
            if max(p.home_win_prob, p.away_win_prob) >= HIGH_CONFIDENCE_THRESHOLD
        ),
        high_confidence_threshold=HIGH_CONFIDENCE_THRESHOLD,
        value_bets_today=sum(1 for p in today_preds if p.best_overall_bet is not None),
        accuracy_30d=accuracy_30d,
        roi_30d=roi_30d,
        settled_matches_30d=settled_count,
    )


@router.get(
    "/predictions/today",
    response_model=List[MatchPredictionRead],
    summary="All of today's predictions excluding the best pick",
)
async def get_today_predictions(db: SessionDep):
    predictions = db.exec(
        select(MatchPrediction)
        .where(MatchPrediction.match_date == date.today())
        .order_by(MatchPrediction.id)
    ).all()
    if not predictions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No predictions for today yet."
        )
    best_id = max(predictions, key=lambda p: max(p.home_win_prob, p.away_win_prob)).id
    return [p for p in predictions if p.id != best_id]


@router.get(
    "/predictions/today/best",
    response_model=MatchPredictionRead,
    summary="Today's match with the highest win probability",
)
async def get_best_prediction_today(db: SessionDep):
    predictions = db.exec(
        select(MatchPrediction).where(MatchPrediction.match_date == date.today())
    ).all()
    if not predictions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No predictions for today yet."
        )
    return max(predictions, key=lambda p: max(p.home_win_prob, p.away_win_prob))


@router.get(
    "/predictions/today/value-bets",
    response_model=List[MatchPredictionRead],
    summary="Today's value bets — positive EV AND Kelly above minimum threshold, sorted by Kelly",
    description=(
        "Only returns matches where best_overall_kelly passes the minimum threshold. "
        "Sorted by fractional Kelly descending — the top pick is the one the model "
        "is most confident has a real edge, not just a fluke high-EV low-probability outcome."
    ),
)
async def get_value_bets_today(db: SessionDep):
    predictions = db.exec(
        select(MatchPrediction).where(
            MatchPrediction.match_date == date.today(),
            MatchPrediction.best_overall_bet.is_not(None),
        )
    ).all()
    if not predictions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No value bets for today. Either no odds loaded or no pick passes the Kelly threshold.",
        )
    predictions.sort(key=lambda p: p.best_overall_kelly or 0, reverse=True)
    return predictions


@router.get(
    "/predictions",
    response_model=List[MatchPredictionRead],
    summary="List all cached predictions",
)
async def list_predictions(
    db: SessionDep,
    match_date: Optional[date] = Query(None),
    home_team: Optional[str] = Query(None),
    away_team: Optional[str] = Query(None),
    value_bets_only: bool = Query(False, description="Only return matches with a valid Kelly pick"),
    offset: int = 0,
    limit: int = Query(default=10, le=100),
):
    query = select(MatchPrediction)
    if match_date:
        query = query.where(MatchPrediction.match_date == match_date)
    if home_team:
        query = query.where(MatchPrediction.home_team.ilike(f"%{home_team}%"))
    if away_team:
        query = query.where(MatchPrediction.away_team.ilike(f"%{away_team}%"))
    if value_bets_only:
        query = query.where(MatchPrediction.best_overall_bet.is_not(None))
    return db.exec(
        query.order_by(MatchPrediction.match_date.desc(), MatchPrediction.id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.get("/predictions/{prediction_id}", response_model=MatchPredictionRead)
async def get_prediction(prediction_id: int, db: SessionDep):
    prediction = db.get(MatchPrediction, prediction_id)
    if not prediction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Prediction {prediction_id} not found"
        )
    return prediction

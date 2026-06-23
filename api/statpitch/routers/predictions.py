import asyncio
from datetime import date
import os

from fastapi import APIRouter, Depends, HTTPException, Query, status
import httpx
from sqlmodel import select

from api.database import SessionDep
from api.security import validate_api_key
from api.statpitch.models import (
    MatchPrediction,
    MatchPredictionBatchCreate,
    MatchPredictionCreate,
    MatchPredictionRead,
    MLPredictionResponse,
)

router = APIRouter()

# Render free tier can take up to 60 s to wake from standby.
_ML_TIMEOUT = 90.0


def _get_ml_url() -> str:
    url = os.getenv("STATPITCH_ML_URL", "").rstrip("/")
    if not url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="STATPITCH_ML_URL is not configured on the server.",
        )
    return url


def _ml_to_db(ml: MLPredictionResponse, target_date: date, is_neutral: bool) -> MatchPrediction:
    """Flatten the nested ML response into a single DB row."""
    return MatchPrediction(
        match_date=target_date,
        home_team=ml.home_team,
        away_team=ml.away_team,
        is_neutral=is_neutral,
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
    )


async def _fetch_one(
    client: httpx.AsyncClient,
    home_team: str,
    away_team: str,
    is_neutral: bool = True,
) -> MLPredictionResponse:
    """Call the ML model for a single match. Raises HTTPException on any failure."""
    try:
        response = await client.get(
            f"{_get_ml_url()}/{home_team}/{away_team}",  # ← teams in the path
            params={"is_neutral": is_neutral},  # ← only this as query param
        )
        response.raise_for_status()
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(
                f"ML model timed out on {home_team} vs {away_team}. "
                "It may still be warming up — wait 60 s and retry."
            ),
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


def _get_existing(
    db: SessionDep,
    target_date: date,
    home_team: str,
    away_team: str,
) -> MatchPrediction | None:
    return db.exec(
        select(MatchPrediction).where(
            MatchPrediction.match_date == target_date,
            MatchPrediction.home_team == home_team,
            MatchPrediction.away_team == away_team,
        )
    ).first()


def _upsert(
    db: SessionDep,
    prediction: MatchPrediction,
    existing: MatchPrediction | None,
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
# WRITE ENDPOINTS  (API-key protected)
# ==============================================================================


@router.post(
    "/predictions",
    response_model=MatchPredictionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(validate_api_key)],
    summary="Fetch and cache a single match prediction",
)
async def create_prediction(
    payload: MatchPredictionCreate,
    db: SessionDep,
    force: bool = Query(
        False, description="Overwrite if a prediction for this match already exists."
    ),
):
    target_date = payload.match_date or date.today()
    existing = _get_existing(db, target_date, payload.home_team, payload.away_team)

    if existing and not force:
        return existing

    async with httpx.AsyncClient(timeout=_ML_TIMEOUT) as client:
        ml_data = await _fetch_one(client, payload.home_team, payload.away_team, payload.is_neutral)

    return _upsert(db, _ml_to_db(ml_data, target_date, payload.is_neutral), existing)


@router.post(
    "/predictions/batch",
    response_model=list[MatchPredictionRead],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(validate_api_key)],
    summary="Fetch and cache all of today's matches in one call",
    description=(
        "Sends all matches to the ML model concurrently, so the total wait time "
        "is the slowest single call, not N × timeout. Idempotent per match: "
        "already-cached matches are skipped unless `force=true`."
    ),
)
async def create_predictions_batch(
    payload: MatchPredictionBatchCreate,
    db: SessionDep,
    force: bool = Query(False, description="Overwrite predictions that already exist."),
):
    if not payload.matches:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="matches list cannot be empty.",
        )

    fallback_date = payload.match_date or date.today()

    # Separate matches that are already cached from those that need a ML call.
    to_fetch: list[tuple[MatchPredictionCreate, date, MatchPrediction | None]] = []
    already_cached: list[MatchPrediction] = []

    for match in payload.matches:
        target_date = match.match_date or fallback_date
        existing = _get_existing(db, target_date, match.home_team, match.away_team)
        if existing and not force:
            already_cached.append(existing)
        else:
            to_fetch.append((match, target_date, existing))

    results: list[MatchPrediction] = list(already_cached)

    if to_fetch:
        # Fire all ML requests concurrently — group stage days can have 4 matches.
        # Total wait ≈ slowest single match, not 4× timeout.
        async with httpx.AsyncClient(timeout=_ML_TIMEOUT) as client:
            ml_responses = await asyncio.gather(
                *[
                    _fetch_one(client, match.home_team, match.away_team, match.is_neutral)
                    for match, _, _ in to_fetch
                ]
            )

        for (match, target_date, existing), ml_data in zip(to_fetch, ml_responses):
            prediction = _upsert(db, _ml_to_db(ml_data, target_date, match.is_neutral), existing)
            results.append(prediction)

    # Return in the same order the matches were submitted.
    order = {(m.home_team, m.away_team): i for i, m in enumerate(payload.matches)}
    results.sort(key=lambda p: order.get((p.home_team, p.away_team), 999))

    return results


@router.delete(
    "/predictions/{prediction_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(validate_api_key)],
)
async def delete_prediction(prediction_id: int, db: SessionDep):
    prediction = db.get(MatchPrediction, prediction_id)
    if not prediction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prediction with id {prediction_id} not found",
        )
    db.delete(prediction)
    db.commit()
    return None


# ==============================================================================
# READ ENDPOINTS  (public)
# ==============================================================================


@router.get(
    "/predictions/today",
    response_model=list[MatchPredictionRead],  # noqa: F821
    summary="Get all cached predictions for today, excluding the best pick",
)
async def get_today_predictions(db: SessionDep):
    predictions = db.exec(
        select(MatchPrediction)
        .where(MatchPrediction.match_date == date.today())
        .order_by(MatchPrediction.id)
    ).all()

    if not predictions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No predictions available for today yet.",
        )

    best_id = max(predictions, key=lambda p: max(p.home_win_prob, p.away_win_prob)).id

    return [p for p in predictions if p.id != best_id]


@router.get(
    "/predictions",
    response_model=list[MatchPredictionRead],
    summary="List all cached predictions",
)
async def list_predictions(
    db: SessionDep,
    match_date: date | None = Query(None, description="Filter by a specific date"),
    home_team: str | None = Query(None, description="Filter by home team name"),
    away_team: str | None = Query(None, description="Filter by away team name"),
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

    query = query.order_by(MatchPrediction.match_date.desc(), MatchPrediction.id)

    return db.exec(query.offset(offset).limit(limit)).all()


@router.get(
    "/predictions/{prediction_id}",
    response_model=MatchPredictionRead,
)
async def get_prediction(prediction_id: int, db: SessionDep):
    prediction = db.get(MatchPrediction, prediction_id)
    if not prediction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prediction with id {prediction_id} not found",
        )
    return prediction


@router.get(
    "/predictions/today/best",
    response_model=MatchPredictionRead,
    summary="Get today's match with the highest win probability",
    description="Returns the match where the stronger side has the highest win probability — the most decisive prediction of the day.",
)
async def get_best_prediction_today(db: SessionDep):
    predictions = db.exec(
        select(MatchPrediction).where(MatchPrediction.match_date == date.today())
    ).all()

    if not predictions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No predictions available for today yet.",
        )

    return max(
        predictions,
        key=lambda p: max(p.home_win_prob, p.away_win_prob),
    )

import logging
from datetime import date, timedelta

import httpx
from sqlmodel import Session, select

from api.database import engine
from api.statpitch.models import MatchPrediction
from api.statpitch.odds_service import fetch_todays_odds
from api.statpitch.scores_service import fetch_recent_scores

log = logging.getLogger("statpitch.scheduler")

# How long Render's ML model gets before we give up on this run.
# It will be retried next cycle.
_ML_TIMEOUT = 90.0


# ==============================================================================
# DATABASE HELPERS  (no FastAPI DI — we create sessions directly)
# ==============================================================================


def _get_unresolved_today(session: Session) -> list[MatchPrediction]:
    today = date.today()
    return session.exec(
        select(MatchPrediction).where(
            MatchPrediction.match_date == today,
            MatchPrediction.actual_result.is_(None),
        )
    ).all()


def _get_all_today(session: Session) -> list[MatchPrediction]:
    return session.exec(
        select(MatchPrediction).where(MatchPrediction.match_date == date.today())
    ).all()


def _get_existing_tomorrow(session: Session) -> list[MatchPrediction]:
    tomorrow = date.today() + timedelta(days=1)
    return session.exec(select(MatchPrediction).where(MatchPrediction.match_date == tomorrow)).all()


# ==============================================================================
# JOB 1 — CHECK AND RECORD RESULTS
# ==============================================================================


async def check_and_record_results() -> None:
    """
    Fetch today's scores, mark completed matches, and trigger tomorrow's
    sync once all of today's matches have a confirmed result.
    """
    log.info("⏱  Running result checker...")

    with Session(engine) as session:
        unresolved = _get_unresolved_today(session)

        if not unresolved:
            log.info("✅ All of today's matches already have results — nothing to do.")
            return

        log.info(f"🔍 {len(unresolved)} match(es) still unresolved, fetching scores...")

        try:
            scores = await fetch_recent_scores(days_back=2)
        except Exception as e:
            log.warning(f"⚠️  Could not fetch scores: {e}. Will retry next cycle.")
            return

        # Build lookup: (home_team, away_team) → actual_result
        score_lookup = {
            (s.home_team, s.away_team): s.actual_result
            for s in scores
            if s.completed and s.actual_result
        }

        newly_resolved = 0
        for prediction in unresolved:
            result = score_lookup.get((prediction.home_team, prediction.away_team))
            if result:
                prediction.actual_result = result
                session.add(prediction)
                log.info(f"✅ {prediction.home_team} vs {prediction.away_team} → {result}")
                newly_resolved += 1

        if newly_resolved:
            session.commit()
            log.info(f"💾 Recorded {newly_resolved} result(s).")

        # Check if ALL of today's matches now have results
        all_today = _get_all_today(session)
        still_unresolved = [p for p in all_today if p.actual_result is None]

        if not still_unresolved and all_today:
            log.info("🏁 All of today's matches resolved — syncing tomorrow's matches now.")
            await sync_tomorrow()


# ==============================================================================
# JOB 2 — SYNC TOMORROW'S MATCHES
# ==============================================================================


async def sync_tomorrow() -> None:
    """
    Fetch tomorrow's matches, odds, and ML predictions, then store them.
    Skips matches already cached for tomorrow.
    """
    tomorrow = date.today() + timedelta(days=1)
    log.info(f"📅 Syncing matches for {tomorrow}...")

    with Session(engine) as session:
        already_cached = _get_existing_tomorrow(session)
        cached_pairs = {(p.home_team, p.away_team) for p in already_cached}
        if cached_pairs:
            log.info(
                f"⏭  {len(cached_pairs)} match(es) already cached for {tomorrow}, skipping those."
            )

    # Temporarily override today's date context by using force-fetching.
    # The odds service always fetches from The Odds API regardless of date,
    # but stores under date.today(). Since we're calling this at midnight-ish,
    # date.today() IS tomorrow relative to when the last match ended.
    try:
        todays_odds = await fetch_todays_odds()
    except Exception as e:
        log.error(f"❌ Failed to fetch tomorrow's odds: {e}")
        return

    if not todays_odds:
        log.info(f"📭 No matches found for {tomorrow} (rest day or competition gap).")
        return

    import os

    ml_url = os.getenv("STATPITCH_ML_URL", "").rstrip("/")
    if not ml_url:
        log.error("❌ STATPITCH_ML_URL not set — cannot fetch ML predictions.")
        return

    from api.statpitch.routers.predictions import _ml_to_db, _upsert
    from api.statpitch.models import MLPredictionResponse

    synced = 0
    async with httpx.AsyncClient(timeout=_ML_TIMEOUT) as client:
        for odds in todays_odds:
            if (odds.home_team, odds.away_team) in cached_pairs:
                continue
            try:
                response = await client.get(
                    f"{ml_url}/{odds.home_team}/{odds.away_team}",
                    params={"is_neutral": True},
                )
                response.raise_for_status()
                ml_data = MLPredictionResponse.model_validate(response.json())

                with Session(engine) as session:
                    existing = session.exec(
                        select(MatchPrediction).where(
                            MatchPrediction.match_date == odds.match_date,
                            MatchPrediction.home_team == odds.home_team,
                            MatchPrediction.away_team == odds.away_team,
                        )
                    ).first()
                    prediction = _ml_to_db(ml_data, odds.match_date, True, odds)
                    _upsert(session, prediction, existing)
                    synced += 1
                    log.info(f"✅ Synced: {odds.home_team} vs {odds.away_team}")

            except Exception as e:
                log.warning(f"⚠️  Failed to sync {odds.home_team} vs {odds.away_team}: {e}")
                continue

    log.info(f"🎉 Tomorrow's sync complete — {synced} match(es) stored.")

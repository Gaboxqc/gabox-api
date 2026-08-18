"""StatPitch read and sync routes.

Collection endpoints return `[]` rather than 404 when a day is empty. An empty
day is a normal answer here, not a missing resource: StatPitch files roughly
88% of fixtures under a matchday placeholder, so a real matchday can legitimately
show nothing under today's date.
"""

import logging
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlmodel import func, select

from api.core.database import SessionDep
from api.core.deps import PageDep
from api.core.security import validate_api_key
from api.statpitch.client import StatPitchError, StatPitchRefusal
from api.statpitch.clock import current_window
from api.statpitch.models import (
    FixtureRead,
    SettledBetRead,
    StatPitchFixture,
    StatPitchSettledBet,
    StatsRead,
    SyncResultRead,
    ThreeDayWindow,
)
from api.statpitch.odds_service import OddsUnavailable
from api.statpitch.stats import BASES, build_stats
from api.statpitch.sync import run_sync

log = logging.getLogger("statpitch.routes")

router = APIRouter()

DayName = Literal["yesterday", "today", "tomorrow"]


def _window_dates() -> ThreeDayWindow:
    window = current_window()
    return ThreeDayWindow(yesterday=window.yesterday, today=window.today, tomorrow=window.tomorrow)


def _fixtures_on(db: SessionDep, day: date) -> list[StatPitchFixture]:
    return db.exec(
        select(StatPitchFixture)
        .where(StatPitchFixture.match_date == day)
        .order_by(StatPitchFixture.commence_time, StatPitchFixture.id)
    ).all()


# ==============================================================================
# SYNC
# ==============================================================================


@router.post(
    "/sync",
    response_model=SyncResultRead,
    dependencies=[Depends(validate_api_key)],
    summary="Fetch, price, settle, bank and prune — the full daily pass",
    description=(
        "Idempotent. Run it at 06:00 UTC, which is midnight in Nicaragua, so the "
        "three-day window rolls over exactly when the frontend's 'today' does. "
        "Running it more often is safe and only refreshes prices."
    ),
)
async def sync(db: SessionDep):
    try:
        report = await run_sync(db)
    except StatPitchRefusal as exc:
        # A refusal is a 200 upstream, but NO_FIXTURE_SOURCE means the fixture
        # artifact is not loaded there — a broken deploy, not a quiet day.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"StatPitch declined ({exc.reason_code}): {exc.reason}",
        ) from exc
    except StatPitchError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except OddsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    return SyncResultRead(
        window=ThreeDayWindow(
            yesterday=report.window.yesterday,
            today=report.window.today,
            tomorrow=report.window.tomorrow,
        ),
        fetched=report.fetched,
        stored=report.stored,
        priced=report.priced,
        unmatched_odds=report.unmatched_odds,
        settled=report.settled,
        ledgered=report.ledgered,
        pruned=report.pruned,
        model_version=report.model_version,
        warnings=report.warnings,
    )


# ==============================================================================
# FIXTURES
# ==============================================================================


@router.get(
    "/fixtures",
    response_model=list[FixtureRead],
    summary="Every cached fixture — yesterday, today and tomorrow",
)
async def list_fixtures(
    db: SessionDep,
    response: Response,
    day: Annotated[DayName | None, Query(description="Restrict to one of the three days")] = None,
    competition_id: Annotated[str | None, Query()] = None,
    value_bets_only: Annotated[bool, Query(description="Only fixtures with a Kelly pick")] = False,
):
    window = _window_dates()
    query = select(StatPitchFixture).where(
        StatPitchFixture.match_date >= window.yesterday,
        StatPitchFixture.match_date <= window.tomorrow,
    )

    if day is not None:
        query = query.where(StatPitchFixture.match_date == getattr(window, day))
    if competition_id:
        query = query.where(StatPitchFixture.competition_id == competition_id)
    if value_bets_only:
        query = query.where(StatPitchFixture.best_overall_bet.is_not(None))

    total = db.exec(select(func.count()).select_from(query.subquery())).one()
    response.headers["X-Total-Count"] = str(int(total))

    return db.exec(
        query.order_by(
            StatPitchFixture.match_date,
            StatPitchFixture.commence_time,
            StatPitchFixture.id,
        )
    ).all()


@router.get(
    "/fixtures/window",
    response_model=ThreeDayWindow,
    summary="The three local dates currently cached",
    description=(
        "What the frontend should label yesterday, today and tomorrow. Computed "
        "in the configured timezone, so it rolls at local midnight rather than "
        "at the server's."
    ),
)
async def get_window():
    return _window_dates()


@router.get(
    "/fixtures/today",
    response_model=list[FixtureRead],
    summary="Today's fixtures, in local time",
)
async def fixtures_today(db: SessionDep):
    return _fixtures_on(db, current_window().today)


@router.get(
    "/fixtures/yesterday",
    response_model=list[FixtureRead],
    summary="Yesterday's fixtures, with results where they have settled",
)
async def fixtures_yesterday(db: SessionDep):
    return _fixtures_on(db, current_window().yesterday)


@router.get(
    "/fixtures/tomorrow",
    response_model=list[FixtureRead],
    summary="Tomorrow's fixtures",
)
async def fixtures_tomorrow(db: SessionDep):
    return _fixtures_on(db, current_window().tomorrow)


@router.get(
    "/fixtures/today/best",
    response_model=FixtureRead,
    summary="Today's most confident fixture",
    description=(
        "Highest home-or-away win probability. This is the model's clearest "
        "call, which is not the same as its best bet — see /fixtures/today/value-bets."
    ),
)
async def best_today(db: SessionDep):
    fixtures = _fixtures_on(db, current_window().today)
    if not fixtures:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No fixtures cached for today.",
        )
    return max(fixtures, key=lambda f: max(f.home_win_prob, f.away_win_prob))


@router.get(
    "/fixtures/today/value-bets",
    response_model=list[FixtureRead],
    summary="Today's positive-edge picks, strongest Kelly first",
    description=(
        "Only fixtures whose best selection clears the minimum fractional Kelly. "
        "Ranking by Kelly rather than EV filters out the high-EV, low-probability "
        "picks that look attractive and are not worth the variance."
    ),
)
async def value_bets_today(db: SessionDep):
    fixtures = db.exec(
        select(StatPitchFixture)
        .where(
            StatPitchFixture.match_date == current_window().today,
            StatPitchFixture.best_overall_bet.is_not(None),
        )
        .order_by(StatPitchFixture.best_overall_kelly.desc())
    ).all()
    return fixtures


@router.get(
    "/fixtures/{fixture_pk}",
    response_model=FixtureRead,
    summary="One cached fixture by primary key",
)
async def get_fixture(fixture_pk: int, db: SessionDep):
    fixture = db.get(StatPitchFixture, fixture_pk)
    if fixture is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Fixture {fixture_pk} is not in the three-day cache.",
        )
    return fixture


# ==============================================================================
# PERFORMANCE
# ==============================================================================


@router.get(
    "/stats",
    response_model=StatsRead,
    summary="Today's shape plus rolling 7- and 30-day ROI for both bet series",
    description=(
        "ROI is flat-stake, read from the permanent ledger rather than the "
        "three-day fixture cache, so the windows are unaffected by retention. "
        "`roi_pct` is null when nothing settled in a window — an empty window "
        "has no ROI, and 0.0 would claim a break-even result that was never "
        "measured."
    ),
)
async def get_stats(db: SessionDep):
    return build_stats(db)


@router.get(
    "/ledger",
    response_model=list[SettledBetRead],
    summary="Settled bets, newest first — the permanent track record",
)
async def get_ledger(
    db: SessionDep,
    response: Response,
    page: PageDep,
    basis: Annotated[str | None, Query(description="1x2 or overall")] = None,
    competition_id: Annotated[str | None, Query()] = None,
):
    if basis is not None and basis not in BASES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"basis must be one of {', '.join(BASES)}.",
        )

    query = select(StatPitchSettledBet)
    if basis:
        query = query.where(StatPitchSettledBet.basis == basis)
    if competition_id:
        query = query.where(StatPitchSettledBet.competition_id == competition_id)

    total = db.exec(select(func.count()).select_from(query.subquery())).one()
    response.headers["X-Total-Count"] = str(int(total))

    return db.exec(
        query.order_by(
            StatPitchSettledBet.match_date.desc(),
            StatPitchSettledBet.id.desc(),
        )
        .offset(page.offset)
        .limit(page.limit)
    ).all()

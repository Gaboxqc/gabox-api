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
from sqlalchemy import case
from sqlmodel import col, func, select

from api.core.database import SessionDep
from api.core.deps import PageDep
from api.core.security import validate_api_key
from api.statpitch.accounts.deps import CallerTier, CurrentAccount
from api.statpitch.bets import build_bets_today
from api.statpitch.client import StatPitchError, StatPitchRefusal
from api.statpitch.clock import current_window
from api.statpitch.competitions import all_competitions
from api.statpitch.models import (
    BetsTodayRead,
    CompetitionRead,
    SettledBetRead,
    StatPitchFixture,
    StatPitchSelection,
    StatPitchSettledBet,
    StatsRead,
    SyncResultRead,
    ThreeDayWindow,
)
from api.statpitch.motd import choose as choose_match_of_the_day
from api.statpitch.motd import fixture_for as match_of_the_day_fixture
from api.statpitch.quota import remaining, unlock, unlocked_ids
from api.statpitch.serialization import (
    FixtureFreeRead,
    FixtureFullRead,
    FixtureTeaserRead,
    serialize_fixture,
    serialize_fixtures,
)
from api.statpitch.stats import BASES, build_stats
from api.statpitch.sync import run_sync
from api.statpitch.tiers import Feature, allows, visible_competitions

# Both shapes, so OpenAPI documents what each tier actually receives. Declaring
# only the free model would be worse than undocumented: FastAPI filters the
# response *down* to the declared model, silently truncating every paid caller
# to the free shape.
FixtureResponse = FixtureFullRead | FixtureFreeRead | FixtureTeaserRead

# How many predictions the caller may still reveal today. `unlimited` for a
# paid tier, so the frontend never has to infer it from a tier name.
QUOTA_HEADER = "X-Predictions-Remaining"

log = logging.getLogger("statpitch.routes")

router = APIRouter()

DayName = Literal["yesterday", "today", "tomorrow"]


def _window_dates() -> ThreeDayWindow:
    window = current_window()
    return ThreeDayWindow(yesterday=window.yesterday, today=window.today, tomorrow=window.tomorrow)


def _scoped(query, tier: str):
    """Restrict a fixture query to the competitions this tier may see.

    Applied in SQL rather than filtered afterwards, so a free caller's
    `X-Total-Count` matches what they actually received. Counting rows somebody
    cannot see and then withholding them makes pagination lie.
    """
    return query.where(col(StatPitchFixture.competition_id).in_(visible_competitions(tier)))


def _report_quota(response: Response, db: SessionDep, account, tier: str, day) -> None:
    """Tell the caller what is left, on every fixture response.

    A header rather than a body field: it belongs to the request, not to any one
    fixture, and putting it in the body would mean repeating it forty times in a
    list.
    """
    left = remaining(db, account, tier, day)
    response.headers[QUOTA_HEADER] = "unlimited" if left is None else str(left)


def _require(feature: Feature, tier: str) -> None:
    """Guard a whole endpoint.

    Used only where a reduced version would be meaningless: there is no partial
    track record worth returning. Depth elsewhere is a response shape, not an
    error — see `serialization`.
    """
    if not allows(tier, feature):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="This is a Pro feature. Upgrade to see it.",
        )


def _fixtures_on(db: SessionDep, day: date, tier: str) -> list[StatPitchFixture]:
    return db.exec(
        _scoped(select(StatPitchFixture).where(StatPitchFixture.match_date == day), tier).order_by(
            StatPitchFixture.commence_time, StatPitchFixture.id
        )
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
    # No 503 for a scores failure. It used to mean "the Odds API key is missing
    # or its quota is spent", which cannot happen now that results are keyless —
    # and a run that stored fixtures and prices but settled nothing is a partial
    # success reported in `warnings`, not a failed request.

    return SyncResultRead(
        window=ThreeDayWindow(
            yesterday=report.window.yesterday,
            today=report.window.today,
            tomorrow=report.window.tomorrow,
        ),
        fetched=report.fetched,
        stored=report.stored,
        priced=report.priced,
        unpriced=report.unpriced,
        selections=report.selections,
        rule_bets=report.rule_bets,
        settled=report.settled,
        ledgered=report.ledgered,
        pruned=report.pruned,
        clubs=report.clubs,
        missing_crests=report.missing_crests,
        match_of_the_day=report.match_of_the_day,
        model_version=report.model_version,
        warnings=report.warnings,
    )


# ==============================================================================
# FIXTURES
# ==============================================================================


@router.get(
    "/competitions",
    response_model=list[CompetitionRead],
    summary="Every competition, with its icon",
    description=(
        "Ungated on purpose: which competitions exist is navigation, not "
        "product. `free_tier` says which of them a free account can actually "
        "see, so the rest can be shown as an upgrade rather than silently "
        "returning nothing."
    ),
)
async def list_competitions(db: SessionDep):
    return [
        CompetitionRead(
            competition_id=row.competition_id,
            name=row.name,
            short_name=row.short_name,
            icon_url=row.icon_url,
            free_tier=row.competition_id in visible_competitions("free"),
        )
        for row in all_competitions(db)
    ]


@router.get(
    "/fixtures",
    response_model=list[FixtureResponse],
    summary="Every cached fixture — yesterday, today and tomorrow",
)
async def list_fixtures(
    db: SessionDep,
    response: Response,
    tier: CallerTier,
    account: CurrentAccount,
    day: Annotated[DayName | None, Query(description="Restrict to one of the three days")] = None,
    competition_id: Annotated[str | None, Query()] = None,
    value_bets_only: Annotated[bool, Query(description="Only fixtures with a Kelly pick")] = False,
):
    window = _window_dates()
    query = _scoped(
        select(StatPitchFixture).where(
            StatPitchFixture.match_date >= window.yesterday,
            StatPitchFixture.match_date <= window.tomorrow,
        ),
        tier,
    )

    if day is not None:
        query = query.where(StatPitchFixture.match_date == getattr(window, day))
    if competition_id:
        query = query.where(StatPitchFixture.competition_id == competition_id)
    if value_bets_only:
        query = query.where(StatPitchFixture.best_overall_bet.is_not(None))

    total = db.exec(select(func.count()).select_from(query.subquery())).one()
    response.headers["X-Total-Count"] = str(int(total))

    rows = db.exec(
        query.order_by(
            StatPitchFixture.match_date,
            StatPitchFixture.commence_time,
            StatPitchFixture.id,
        )
    ).all()

    # Listing never spends an unlock. Browsing what is on today is not the thing
    # being sold, and charging for it would make the fixture list useless.
    _report_quota(response, db, account, tier, current_window().today)
    return serialize_fixtures(rows, tier, unlocked_ids=unlocked_ids(db, account))


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
    response_model=list[FixtureResponse],
    summary="Today's fixtures, in local time",
)
async def fixtures_today(
    db: SessionDep, response: Response, tier: CallerTier, account: CurrentAccount
):
    _report_quota(response, db, account, tier, current_window().today)
    return serialize_fixtures(
        _fixtures_on(db, current_window().today, tier),
        tier,
        unlocked_ids=unlocked_ids(db, account),
    )


@router.get(
    "/fixtures/yesterday",
    response_model=list[FixtureResponse],
    summary="Yesterday's fixtures, with results where they have settled",
)
async def fixtures_yesterday(
    db: SessionDep, response: Response, tier: CallerTier, account: CurrentAccount
):
    _report_quota(response, db, account, tier, current_window().today)
    return serialize_fixtures(
        _fixtures_on(db, current_window().yesterday, tier),
        tier,
        unlocked_ids=unlocked_ids(db, account),
    )


@router.get(
    "/fixtures/tomorrow",
    response_model=list[FixtureResponse],
    summary="Tomorrow's fixtures",
)
async def fixtures_tomorrow(
    db: SessionDep, response: Response, tier: CallerTier, account: CurrentAccount
):
    _report_quota(response, db, account, tier, current_window().today)
    return serialize_fixtures(
        _fixtures_on(db, current_window().tomorrow, tier),
        tier,
        unlocked_ids=unlocked_ids(db, account),
    )


@router.get(
    "/fixtures/today/best",
    response_model=FixtureResponse,
    summary="Today's most confident fixture",
    description=(
        "Highest home-or-away win probability. This is the model's clearest "
        "call, which is not the same as its best bet — see /fixtures/today/value-bets."
    ),
)
async def best_today(db: SessionDep, response: Response, tier: CallerTier, account: CurrentAccount):
    """Every tier gets this — "Match of the Day pick" is its own line on the
    free column of the pricing page, listed beside the three daily predictions
    rather than counted among them. So it is always revealed and never spends an
    unlock; free simply sees it at free depth.

    The pick is chosen once by the day's first sync and then left alone, so it
    does not wander as prices move. Recomputing here is only the fallback for a
    day whose sync has not run yet — and it deliberately does not write, because
    a read request is the wrong place to decide something the whole product then
    has to agree on.
    """
    today = current_window().today
    best = match_of_the_day_fixture(db, today)

    if best is None:
        fixtures = _fixtures_on(db, today, tier)
        if not fixtures:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No fixtures cached for today.",
            )
        best = choose_match_of_the_day(fixtures) or max(
            fixtures, key=lambda f: max(f.home_win_prob, f.away_win_prob)
        )

    _report_quota(response, db, account, tier, today)
    return serialize_fixture(best, tier, unlocked=True)


@router.get(
    "/bets/today",
    response_model=BetsTodayRead,
    summary="StatPitch's own pick for today, with the caveats that qualify it",
    description=(
        "Served from cache, not proxied: the upstream instance sleeps after "
        "fifteen minutes idle, and a proxied read would drop the caveats at "
        "exactly the moment somebody is looking at a bet.\n\n"
        "**`caveat` is never null while `bets` is non-empty, and must be "
        "rendered.** The rule behind these picks has five seasons of measured "
        "closing-line value but runs on a price panel that has none, so its "
        "calibration is inherited rather than re-measured. A pick shown without "
        "that statement claims more than the evidence supports.\n\n"
        "An empty day is a normal answer, not a 404 — the rule fires only where "
        "a book misprices against the benchmark, which is most days not at all. "
        "`reason` and `empty_because` say why."
    ),
)
async def bets_today(db: SessionDep, tier: CallerTier):
    """Pro and above. A staked recommendation is the edge indicator itself."""
    _require(Feature.EDGE_INDICATORS, tier)
    return build_bets_today(db, current_window().today, tier)


@router.get(
    "/fixtures/today/value-bets",
    response_model=list[FixtureResponse],
    summary="Today's positive-edge picks, strongest stake first",
    description=(
        "`basis=overall` (the default) and `basis=1x2` are **our** selections: "
        "fixtures whose best pick clears the minimum fractional Kelly, ranked by "
        "Kelly rather than EV so the high-EV, low-probability picks that are not "
        "worth the variance fall away.\n\n"
        "`basis=rule` is **StatPitch's** own selection rule instead, ranked by "
        "the stake it assigned. The two are different strategies measured as "
        "separate series — see `/stats` — so this parameter switches between "
        "them rather than merging them."
    ),
)
async def value_bets_today(
    db: SessionDep,
    tier: CallerTier,
    basis: Annotated[str, Query(description="overall (default), 1x2, or rule")] = "overall",
):
    """Pro and above. This endpoint *is* the edge indicator, so a free version
    would either be empty or give away the thing being sold.

    `rule` deliberately does not become the default. This endpoint has been
    measuring our own Kelly selections since it existed, and quietly repointing
    it would rewrite what its numbers have always meant.
    """
    _require(Feature.EDGE_INDICATORS, tier)

    if basis not in BASES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"basis must be one of {', '.join(BASES)}.",
        )

    today = current_window().today

    if basis == "rule":
        query = (
            select(StatPitchFixture)
            .join(
                StatPitchSelection,
                col(StatPitchSelection.fixture_id) == col(StatPitchFixture.fixture_id),
            )
            .where(
                StatPitchFixture.match_date == today,
                StatPitchSelection.stake_fraction > 0,
            )
            .order_by(col(StatPitchSelection.stake_fraction).desc())
        )
    else:
        pick = StatPitchFixture.best_bet if basis == "1x2" else StatPitchFixture.best_overall_bet
        # The 1X2 series has no stored Kelly of its own — only `best_bet` and
        # its price — so the stake is read from whichever side was picked.
        # Ranking by `kelly_home` regardless would sort a draw pick by a number
        # belonging to a different selection, and `best_overall_kelly` is the
        # wrong column too the moment a market outside 1X2 can win it.
        rank = (
            case(
                (StatPitchFixture.best_bet == "home_win", StatPitchFixture.kelly_home),
                (StatPitchFixture.best_bet == "draw", StatPitchFixture.kelly_draw),
                (StatPitchFixture.best_bet == "away_win", StatPitchFixture.kelly_away),
            )
            if basis == "1x2"
            else StatPitchFixture.best_overall_kelly
        )
        query = (
            select(StatPitchFixture)
            .where(StatPitchFixture.match_date == today, pick.is_not(None))
            .order_by(rank.desc())
        )

    # Pro and above only, so there is no allowance to consult.
    return serialize_fixtures(db.exec(query).all(), tier)


@router.get(
    "/fixtures/{fixture_pk}",
    response_model=FixtureResponse,
    summary="One cached fixture by primary key",
)
async def get_fixture(
    fixture_pk: int,
    db: SessionDep,
    response: Response,
    tier: CallerTier,
    account: CurrentAccount,
):
    """Opening a fixture is what reveals its prediction, and what spends one of
    a free account's three daily unlocks.

    Running out is not an error: the fixture still comes back, with `locked`
    true and no probabilities, so the page can render the upsell in place. A 402
    here would blank a screen the reader was already looking at.
    """
    fixture = db.get(StatPitchFixture, fixture_pk)
    # A competition this tier cannot see is reported as absent rather than as
    # forbidden. 403 would confirm the fixture exists, and which fixtures exist
    # in the other seven competitions is part of what Pro is buying.
    if fixture is None or fixture.competition_id not in visible_competitions(tier):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Fixture {fixture_pk} is not in the three-day cache.",
        )

    today = current_window().today
    revealed = unlock(db, account, tier, fixture.fixture_id, today)
    _report_quota(response, db, account, tier, today)
    return serialize_fixture(fixture, tier, unlocked=revealed)


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
async def get_stats(db: SessionDep, tier: CallerTier):
    _require(Feature.LEDGER_ROI, tier)
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
    tier: CallerTier,
    basis: Annotated[str | None, Query(description="1x2 or overall")] = None,
    competition_id: Annotated[str | None, Query()] = None,
):
    _require(Feature.LEDGER_ROI, tier)

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

"""Rolling performance, read from the ledger.

This reads `statpitch_settled_bet`, never `statpitch_fixture`. That is the
point of the split: fixtures are deleted after three days, so any ROI computed
from them could only ever look back three days. The ledger is permanent, so a
seven- and thirty-day window survives the retention policy.

ROI is flat-stake — one unit per bet, return per unit staked — which keeps the
two series comparable. `kelly_fraction` is stored alongside each row, so a
stake-weighted variant can be derived later without rewriting settled history.
"""

from datetime import date, timedelta

from sqlalchemy import Integer, cast
from sqlmodel import Session, func, select

from api.core.config import settings
from api.statpitch.clock import current_window, today_local
from api.statpitch.models import (
    BasisRoi,
    StatPitchFixture,
    StatPitchSettledBet,
    StatsRead,
    ThreeDayWindow,
    WindowRoi,
)
from api.statpitch.pricing import HIGH_CONFIDENCE_THRESHOLD

WEEK_DAYS = 7
MONTH_DAYS = 30

# The two parallel track records, in the order the frontend shows them.
BASES: tuple[str, ...] = ("1x2", "overall")


def window_start(days: int, today: date | None = None) -> date:
    """First day of a rolling window that ends today, inclusive both ends.

    `days=7` spans today and the six days before it — seven distinct days, not
    eight.
    """
    return (today or today_local()) - timedelta(days=days - 1)


def roi_for(session: Session, basis: str, days: int, today: date | None = None) -> WindowRoi:
    """Flat-stake ROI for one series over one rolling window."""
    today = today or today_local()
    start = window_start(days, today)

    totals = session.exec(
        select(
            func.count(StatPitchSettledBet.id),
            # SQLite has no native boolean, so summing `won` directly is not
            # portable between it and PostgreSQL.
            func.sum(cast(StatPitchSettledBet.won, Integer)),
            func.sum(StatPitchSettledBet.stake_units),
            func.sum(StatPitchSettledBet.pnl_units),
        ).where(
            StatPitchSettledBet.basis == basis,
            StatPitchSettledBet.match_date >= start,
            StatPitchSettledBet.match_date <= today,
        )
    ).one()

    bets = int(totals[0] or 0)
    wins = int(totals[1] or 0)
    staked = float(totals[2] or 0.0)
    pnl = float(totals[3] or 0.0)

    # An empty window has no ROI. Reporting 0.0% would claim break-even
    # performance that was never measured.
    roi_pct = round(pnl / staked * 100, 2) if bets and staked else None
    hit_rate = round(wins / bets * 100, 1) if bets else None

    return WindowRoi(
        bets=bets,
        wins=wins,
        staked_units=round(staked, 4),
        returned_units=round(staked + pnl, 4),
        pnl_units=round(pnl, 4),
        roi_pct=roi_pct,
        hit_rate_pct=hit_rate,
    )


def build_stats(session: Session) -> StatsRead:
    """The stats bar: today's shape plus both series over both windows."""
    window = current_window()

    today_fixtures = session.exec(
        select(StatPitchFixture).where(StatPitchFixture.match_date == window.today)
    ).all()

    tomorrow_count = session.exec(
        select(func.count(StatPitchFixture.id)).where(
            StatPitchFixture.match_date == window.tomorrow
        )
    ).one()

    return StatsRead(
        generated_for=window.today,
        timezone=settings.statpitch_timezone,
        window=ThreeDayWindow(
            yesterday=window.yesterday,
            today=window.today,
            tomorrow=window.tomorrow,
        ),
        fixtures_today=len(today_fixtures),
        fixtures_tomorrow=int(tomorrow_count or 0),
        # How many of today's fixtures sit on a real published kickoff rather
        # than a matchday placeholder. Roughly 12% of the list upstream.
        date_confirmed_today=sum(1 for f in today_fixtures if f.date_confirmed),
        high_confidence_today=sum(
            1
            for f in today_fixtures
            if max(f.home_win_prob, f.away_win_prob) >= HIGH_CONFIDENCE_THRESHOLD
        ),
        high_confidence_threshold=HIGH_CONFIDENCE_THRESHOLD,
        value_bets_today=sum(1 for f in today_fixtures if f.best_overall_bet is not None),
        roi=[
            BasisRoi(
                basis=basis,
                week=roi_for(session, basis, WEEK_DAYS, window.today),
                month=roi_for(session, basis, MONTH_DAYS, window.today),
            )
            for basis in BASES
        ],
    )

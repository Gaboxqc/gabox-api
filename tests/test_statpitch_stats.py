"""Rolling ROI, and the reason it is read from the ledger."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from api.statpitch.clock import today_local
from api.statpitch.models import StatPitchSettledBet
from api.statpitch.stats import MONTH_DAYS, WEEK_DAYS, roi_for, window_start


def _bet(days_ago: int, *, won: bool, odds: float = 2.0, basis: str = "1x2", n: int = 0):
    match_date = today_local() - timedelta(days=days_ago)
    return StatPitchSettledBet(
        fixture_id=f"ESP.LALIGA|2026-2027|H{days_ago}-{basis}-{n}|A{days_ago}-{basis}-{n}",
        competition_id="ESP.LALIGA",
        home_team="Home",
        away_team="Away",
        match_date=match_date,
        settled_at=datetime.now(UTC),
        basis=basis,
        selection="home_win",
        probability=0.55,
        odds_taken=odds,
        stake_units=1.0,
        won=won,
        pnl_units=round((odds - 1) if won else -1.0, 4),
        home_score=1 if won else 0,
        away_score=0 if won else 1,
        model_version="goals-test-0001",
    )


class TestWindowStart:
    def test_seven_days_spans_seven_distinct_days(self):
        start = window_start(WEEK_DAYS)
        assert (today_local() - start).days == WEEK_DAYS - 1


class TestRoi:
    def test_empty_window_has_no_roi(self, engine):
        with Session(engine) as db:
            result = roi_for(db, "1x2", WEEK_DAYS)

        # Not 0.0 — that would claim a break-even result never measured.
        assert result.bets == 0
        assert result.roi_pct is None
        assert result.hit_rate_pct is None

    def test_break_even_at_evens(self, engine):
        with Session(engine) as db:
            db.add(_bet(1, won=True, n=1))
            db.add(_bet(2, won=False, n=2))
            db.commit()
            result = roi_for(db, "1x2", WEEK_DAYS)

        assert result.bets == 2
        assert result.wins == 1
        assert result.staked_units == pytest.approx(2.0)
        assert result.roi_pct == pytest.approx(0.0)
        assert result.hit_rate_pct == pytest.approx(50.0)

    def test_profitable_run(self, engine):
        with Session(engine) as db:
            db.add(_bet(1, won=True, odds=3.0, n=1))
            db.add(_bet(2, won=False, odds=3.0, n=2))
            db.commit()
            result = roi_for(db, "1x2", WEEK_DAYS)

        # Staked 2, returned 3 -> +50%.
        assert result.pnl_units == pytest.approx(1.0)
        assert result.roi_pct == pytest.approx(50.0)
        assert result.returned_units == pytest.approx(3.0)

    def test_week_window_excludes_older_bets(self, engine):
        with Session(engine) as db:
            db.add(_bet(2, won=True, n=1))
            db.add(_bet(20, won=True, n=2))
            db.commit()

            week = roi_for(db, "1x2", WEEK_DAYS)
            month = roi_for(db, "1x2", MONTH_DAYS)

        assert week.bets == 1
        assert month.bets == 2

    def test_beyond_the_month_window_is_excluded(self, engine):
        with Session(engine) as db:
            db.add(_bet(45, won=True, n=1))
            db.commit()
            assert roi_for(db, "1x2", MONTH_DAYS).bets == 0

    def test_the_two_series_are_measured_separately(self, engine):
        with Session(engine) as db:
            db.add(_bet(1, won=True, odds=3.0, basis="1x2", n=1))
            db.add(_bet(1, won=False, odds=3.0, basis="overall", n=2))
            db.commit()

            one_x_two = roi_for(db, "1x2", WEEK_DAYS)
            overall = roi_for(db, "overall", WEEK_DAYS)

        # A winner at 3.0 returns 2 units of profit on 1 staked.
        assert one_x_two.roi_pct == pytest.approx(200.0)
        assert overall.roi_pct == pytest.approx(-100.0)


class TestRoiSurvivesPruning:
    def test_roi_is_unaffected_by_an_empty_fixture_cache(self, engine):
        """The whole reason for the two-table split.

        Nothing is written to `statpitch_fixture` here at all — the fixtures
        these bets came from are long pruned — and the 30-day ROI is still
        exactly right.
        """
        with Session(engine) as db:
            db.add(_bet(10, won=True, odds=2.0, n=1))
            db.add(_bet(20, won=True, odds=2.0, n=2))
            db.commit()
            result = roi_for(db, "1x2", MONTH_DAYS)

        assert result.bets == 2
        assert result.roi_pct == pytest.approx(100.0)

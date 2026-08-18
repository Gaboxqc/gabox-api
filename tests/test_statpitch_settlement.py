"""Settling, banking the ledger, and the pruning guard that protects it."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from api.statpitch.clock import Window, to_local_date, today_local
from api.statpitch.models import StatPitchSettledBet
from api.statpitch.scores_service import MatchScore
from api.statpitch.settlement import (
    apply_scores,
    bank_ledger,
    prune_fixtures,
    selection_won,
)


class TestSelectionWon:
    @pytest.mark.parametrize(
        "selection,home,away,expected",
        [
            ("home_win", 2, 0, True),
            ("home_win", 1, 1, False),
            ("draw", 1, 1, True),
            ("away_win", 0, 3, True),
            ("over_1_5", 1, 1, True),
            ("over_1_5", 1, 0, False),
            ("under_1_5", 1, 0, True),
            ("over_2_5", 2, 1, True),
            ("under_2_5", 1, 1, True),
            ("over_3_5", 2, 2, True),
            ("under_3_5", 2, 1, True),
            ("btts_yes", 1, 1, True),
            ("btts_yes", 3, 0, False),
            ("btts_no", 3, 0, True),
        ],
    )
    def test_settles_every_market(self, selection, home, away, expected):
        assert selection_won(selection, home, away) is expected

    def test_unknown_selection_raises(self):
        with pytest.raises(ValueError, match="unknown selection"):
            selection_won("asian_handicap_-0_5", 1, 0)

    def test_goal_counts_are_required_not_just_the_winner(self):
        """A 1X2 outcome cannot settle a totals market.

        Both of these are home wins, and they settle over_2_5 differently —
        which is why the scores service records goals rather than a result.
        """
        assert selection_won("over_2_5", 3, 1) is True
        assert selection_won("over_2_5", 1, 0) is False


class TestApplyScores:
    def _score(self, home, away, home_score, away_score, **kwargs):
        return MatchScore(
            competition_id=kwargs.get("competition_id", "ESP.LALIGA"),
            home_team=home,
            away_team=away,
            commence_time=kwargs.get("commence_time", datetime.now(UTC)),
            completed=kwargs.get("completed", True),
            home_score=home_score,
            away_score=away_score,
        )

    def test_settles_a_matching_fixture(self, engine, make_fixture):
        fixture = make_fixture(home_team="FC Barcelona", away_team="Athletic Club")
        with Session(engine) as db:
            db.add(fixture)
            db.commit()
            db.refresh(fixture)

            settled = apply_scores(db, [fixture], [self._score("Barcelona", "Athletic Club", 2, 1)])
            assert settled == 1
            assert fixture.home_score == 2
            assert fixture.actual_result == "home_win"
            assert fixture.settled_at is not None

    def test_ignores_matches_still_in_progress(self, engine, make_fixture):
        fixture = make_fixture(home_team="FC Barcelona", away_team="Athletic Club")
        with Session(engine) as db:
            db.add(fixture)
            db.commit()
            db.refresh(fixture)

            settled = apply_scores(
                db,
                [fixture],
                [self._score("Barcelona", "Athletic Club", 1, 0, completed=False)],
            )
            assert settled == 0
            assert fixture.actual_result is None

    def test_will_not_settle_from_a_different_competition(self, engine, make_fixture):
        fixture = make_fixture(
            competition_id="ESP.LALIGA", home_team="Valencia CF", away_team="Levante UD"
        )
        with Session(engine) as db:
            db.add(fixture)
            db.commit()
            db.refresh(fixture)

            settled = apply_scores(
                db,
                [fixture],
                [self._score("Valencia", "Levante", 1, 0, competition_id="ITA.SERIEA")],
            )
            assert settled == 0

    def test_will_not_settle_from_a_distant_date(self, engine, make_fixture):
        """Guards the cup-replay case: same clubs, different leg."""
        fixture = make_fixture(home_team="Valencia CF", away_team="Levante UD")
        far_off = datetime.now(UTC) + timedelta(days=9)
        with Session(engine) as db:
            db.add(fixture)
            db.commit()
            db.refresh(fixture)

            settled = apply_scores(
                db, [fixture], [self._score("Valencia", "Levante", 1, 0, commence_time=far_off)]
            )
            assert settled == 0


class TestBankLedger:
    def _settled(self, make_fixture, **kwargs):
        defaults = {
            "home_score": 2,
            "away_score": 1,
            "actual_result": "home_win",
            "settled_at": datetime.now(UTC),
        }
        defaults.update(kwargs)
        return make_fixture(**defaults)

    def test_writes_one_row_per_series(self, engine, make_fixture):
        fixture = self._settled(
            make_fixture,
            odds_home=2.5,
            best_bet="home_win",
            best_bet_odds=2.5,
            odds_over_2_5=2.6,
            best_overall_bet="over_2_5",
            best_overall_odds=2.6,
            best_overall_kelly=0.05,
        )
        with Session(engine) as db:
            db.add(fixture)
            db.commit()
            db.refresh(fixture)

            assert bank_ledger(db, [fixture]) == 2
            rows = db.exec(select(StatPitchSettledBet)).all()

        assert {row.basis for row in rows} == {"1x2", "overall"}
        one_x_two = next(r for r in rows if r.basis == "1x2")
        assert one_x_two.selection == "home_win"
        assert one_x_two.won is True
        # 2.5 at one unit staked returns 1.5 profit.
        assert one_x_two.pnl_units == pytest.approx(1.5)

    def test_a_loss_costs_exactly_the_stake(self, engine, make_fixture):
        fixture = self._settled(
            make_fixture,
            home_score=0,
            away_score=1,
            actual_result="away_win",
            odds_home=2.5,
            best_bet="home_win",
        )
        with Session(engine) as db:
            db.add(fixture)
            db.commit()
            db.refresh(fixture)
            bank_ledger(db, [fixture])
            row = db.exec(select(StatPitchSettledBet)).one()

        assert row.won is False
        assert row.pnl_units == pytest.approx(-1.0)

    def test_running_twice_does_not_double_count(self, engine, make_fixture):
        fixture = self._settled(make_fixture, odds_home=2.5, best_bet="home_win")
        with Session(engine) as db:
            db.add(fixture)
            db.commit()
            db.refresh(fixture)

            assert bank_ledger(db, [fixture]) == 1
            assert bank_ledger(db, [fixture]) == 0
            assert len(db.exec(select(StatPitchSettledBet)).all()) == 1

    def test_a_fixture_with_no_pick_is_still_marked_banked(self, engine, make_fixture):
        """Otherwise it would block pruning forever."""
        fixture = self._settled(make_fixture)
        with Session(engine) as db:
            db.add(fixture)
            db.commit()
            db.refresh(fixture)

            assert bank_ledger(db, [fixture]) == 0
            assert fixture.ledgered is True

    def test_unsettled_fixtures_are_skipped(self, engine, make_fixture):
        fixture = make_fixture(odds_home=2.5, best_bet="home_win")
        with Session(engine) as db:
            db.add(fixture)
            db.commit()
            db.refresh(fixture)

            assert bank_ledger(db, [fixture]) == 0
            assert fixture.ledgered is False


class TestPruning:
    def _window(self):
        today = today_local()
        return Window(
            yesterday=today - timedelta(days=1),
            today=today,
            tomorrow=today + timedelta(days=1),
        )

    def test_keeps_everything_inside_the_window(self, engine, make_fixture):
        window = self._window()
        with Session(engine) as db:
            for day in (window.yesterday, window.today, window.tomorrow):
                db.add(make_fixture(match_date=day, source_date=day))
            db.commit()

            pruned, abandoned, _ = prune_fixtures(db, window)

        assert (pruned, abandoned) == (0, 0)

    def test_drops_a_banked_fixture_that_fell_out(self, engine, make_fixture):
        window = self._window()
        old = window.yesterday - timedelta(days=1)
        with Session(engine) as db:
            db.add(
                make_fixture(
                    match_date=old,
                    source_date=old,
                    actual_result="home_win",
                    home_score=1,
                    away_score=0,
                    ledgered=True,
                )
            )
            db.commit()

            pruned, abandoned, _ = prune_fixtures(db, window)

        assert (pruned, abandoned) == (1, 0)

    def test_refuses_to_drop_a_fixture_the_ledger_has_not_banked(self, engine, make_fixture):
        """The guarantee that makes retention and ROI independent."""
        window = self._window()
        old = window.yesterday - timedelta(days=1)
        with Session(engine) as db:
            db.add(
                make_fixture(match_date=old, source_date=old, odds_home=2.5, best_bet="home_win")
            )
            db.commit()

            pruned, abandoned, _ = prune_fixtures(db, window)
            remaining = db.exec(select(StatPitchSettledBet)).all()

        assert (pruned, abandoned) == (0, 0)
        assert remaining == []

    def test_abandons_a_result_that_never_arrived(self, engine, make_fixture):
        window = self._window()
        ancient = today_local() - timedelta(days=30)
        with Session(engine) as db:
            db.add(make_fixture(match_date=ancient, source_date=ancient))
            db.commit()

            pruned, abandoned, warnings = prune_fixtures(db, window)

        assert (pruned, abandoned) == (0, 1)
        assert warnings and "no result" in warnings[0]


class TestLocalDates:
    def test_late_utc_kickoff_belongs_to_the_previous_nicaragua_day(self):
        """The bug the clock module exists to prevent.

        01:00 UTC on the 18th is 19:00 on the 17th in Nicaragua, so the fixture
        belongs to the 17th — the day the frontend is still showing.
        """
        instant = datetime(2026, 8, 18, 1, 0, tzinfo=UTC)
        assert to_local_date(instant).isoformat() == "2026-08-17"

    def test_naive_timestamps_are_read_as_utc(self):
        naive = datetime(2026, 8, 18, 1, 0)
        assert to_local_date(naive).isoformat() == "2026-08-17"

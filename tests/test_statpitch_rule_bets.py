"""Taking StatPitch's prices, and banking its rule as a separate series.

The guards are most of what is worth testing here. A price that nobody offered
must never reach a column the ledger later reads as a price taken, and a market
that can push must never reach a settler that has no notion of a push.
"""

from datetime import UTC, datetime

import pytest
from sqlmodel import Session, select

from api.statpitch.clock import to_local_date, today_local
from api.statpitch.models import SPSelection, StatPitchSelection, StatPitchSettledBet
from api.statpitch.settlement import (
    _rule_ledger_row,
    _rule_selection,
    bank_ledger,
)
from api.statpitch.sync import _attach_kickoff, _attach_odds, _is_bettable


def _sp(selection: str = "1x2_home", **overrides) -> SPSelection:
    """A StatPitch selection with the shape the live service actually sends."""
    data = {
        "fixture_id": "ESP.LALIGA|2026-2027|Home 1|Away 1",
        "selection": selection,
        "market_family": selection.split("_")[0],
        "odds": 1.75,
        "reference_odds": 1.68,
        "consensus_odds": 1.65,
        "fair_odds": 1.7295,
        "p_model": 0.7319,
        "q_fair": 0.5782,
        "p_used": 0.5782,
        "stake_fraction": 0.0,
    }
    data.update(overrides)
    return SPSelection.model_validate(data)


def _stored(**overrides) -> StatPitchSelection:
    data = {
        "fixture_id": "ESP.LALIGA|2026-2027|Home 1|Away 1",
        "selection": "1x2_home",
        "our_selection": "home_win",
        "market_family": "1x2",
        "odds": 1.75,
        "p_used": 0.5782,
        "stake_fraction": 0.00125,
        "config_status": "experimental",
        "selection_rule_status": "experimental",
    }
    data.update(overrides)
    return StatPitchSelection(**data)


class TestBettable:
    def test_a_real_quote_is_bettable(self):
        assert _is_bettable(_sp())

    def test_a_model_priced_row_is_not(self):
        """Its `odds` is 1 / p_model — StatPitch's opinion, not an offer."""
        assert not _is_bettable(_sp(pricing="model", odds=1.3663))

    def test_a_market_priced_row_still_is(self):
        assert _is_bettable(_sp(pricing="market"))

    @pytest.mark.parametrize("odds", [None, 1.0, 0.5])
    def test_an_impossible_price_is_not(self, odds):
        assert not _is_bettable(_sp(odds=odds))


class TestAttachOdds:
    def test_the_three_1x2_quotes_land_on_their_columns(self, make_fixture):
        fixture = make_fixture()
        priced = _attach_odds(
            fixture,
            [
                _sp("1x2_home", odds=1.75),
                _sp("1x2_draw", odds=3.6),
                _sp("1x2_away", odds=4.2),
            ],
        )

        assert priced
        assert (fixture.odds_home, fixture.odds_draw, fixture.odds_away) == (1.75, 3.6, 4.2)

    def test_only_the_best_quote_is_taken_never_the_other_three_prices(self, make_fixture):
        """`odds` is the bettable one. The benchmark, the consensus and the
        de-vigged fair price are not offers and must not be stored as one."""
        fixture = make_fixture()
        _attach_odds(fixture, [_sp("1x2_home", odds=1.75)])

        assert fixture.odds_home == 1.75
        assert fixture.odds_home not in (1.68, 1.65, 1.7295)

    def test_a_model_priced_row_never_reaches_a_price_column(self, make_fixture):
        """The single most dangerous merge available: this column is what
        `_ledger_row` later records as `odds_taken`."""
        fixture = make_fixture()
        priced = _attach_odds(fixture, [_sp("1x2_home", pricing="model", odds=1.3663)])

        assert not priced
        assert fixture.odds_home is None

    def test_an_untranslatable_selection_is_ignored(self, make_fixture):
        fixture = make_fixture()

        assert not _attach_odds(fixture, [_sp("handicap_home_minus_1", odds=2.1)])

    def test_pricing_nothing_reports_nothing(self, make_fixture):
        assert not _attach_odds(make_fixture(), [])


class TestAttachKickoff:
    def test_the_instant_sets_both_the_time_and_the_local_day(self, make_fixture):
        """StatPitch's own `kickoff` is a bare "20:00" with no zone, so this is
        the only thing that can file a fixture under the right local day."""
        fixture = make_fixture()
        kickoff = datetime(2026, 9, 2, 18, 45, tzinfo=UTC)

        _attach_kickoff(fixture, kickoff)

        assert fixture.commence_time == kickoff
        assert fixture.match_date == to_local_date(kickoff)

    def test_a_late_utc_kickoff_files_under_the_previous_nicaraguan_day(self, make_fixture):
        """The off-by-one this whole module exists to prevent: 02:00 UTC is
        still the evening before in Managua."""
        fixture = make_fixture()
        _attach_kickoff(fixture, datetime(2026, 9, 3, 2, 0, tzinfo=UTC))

        assert fixture.match_date.day == 2


class TestRuleSelection:
    @pytest.fixture(name="persisted")
    def persisted_fixture(self, engine, seed_fixtures, make_fixture):
        fixture = seed_fixtures(make_fixture(home_score=2, away_score=0))[0]

        def _add(**overrides) -> StatPitchSelection:
            row = _stored(fixture_id=fixture.fixture_id, **overrides)
            with Session(engine) as db:
                db.add(row)
                db.commit()
            return row

        return fixture, _add

    def test_the_staked_row_is_chosen(self, engine, persisted):
        fixture, add = persisted
        add()

        with Session(engine) as db:
            chosen = _rule_selection(db, fixture)

        assert chosen is not None
        assert chosen.our_selection == "home_win"

    def test_an_unstaked_row_is_not_a_bet(self, engine, persisted):
        """`stake_fraction` is what separates analysis from recommendation."""
        fixture, add = persisted
        add(stake_fraction=0.0)

        with Session(engine) as db:
            assert _rule_selection(db, fixture) is None

    def test_a_model_priced_row_is_never_banked(self, engine, persisted):
        fixture, add = persisted
        add(pricing="model", odds=1.3663)

        with Session(engine) as db:
            assert _rule_selection(db, fixture) is None

    def test_a_handicap_is_never_banked(self, engine, persisted):
        """`selection_won` has no notion of a push, so a handicap would settle
        a returned stake as a loss."""
        fixture, add = persisted
        add(selection="handicap_home", market_family="handicap", our_selection=None)

        with Session(engine) as db:
            assert _rule_selection(db, fixture) is None

    def test_an_untranslated_row_is_never_banked(self, engine, persisted):
        fixture, add = persisted
        add(our_selection=None)

        with Session(engine) as db:
            assert _rule_selection(db, fixture) is None


class TestRuleLedgerRow:
    def test_it_records_statpitchs_own_numbers(self, make_fixture):
        """Not ours. Scoring their rule against our probability and our price
        would measure neither of the two things."""
        fixture = make_fixture(home_score=2, away_score=0)
        row = _rule_ledger_row(fixture, _stored())

        assert row is not None
        assert row.basis == "rule"
        assert row.probability == 0.5782
        assert row.odds_taken == 1.75
        assert row.kelly_fraction == 0.00125

    def test_the_stake_stays_one_flat_unit(self, make_fixture):
        """So all three series stay comparable, with the fraction alongside so
        a stake-weighted ROI is still derivable."""
        row = _rule_ledger_row(make_fixture(home_score=2, away_score=0), _stored())

        assert row.stake_units == 1.0
        assert row.pnl_units == pytest.approx(0.75)

    def test_a_loss_costs_the_stake(self, make_fixture):
        row = _rule_ledger_row(make_fixture(home_score=0, away_score=1), _stored())

        assert not row.won
        assert row.pnl_units == -1.0

    def test_provenance_is_frozen_onto_the_row(self, make_fixture):
        """When the rule is promoted to `fitted`, this row must still read as
        what it was recommended under."""
        row = _rule_ledger_row(make_fixture(home_score=1, away_score=0), _stored())

        assert row.config_status == "experimental"
        assert row.selection_rule_status == "experimental"

    def test_an_unsettled_fixture_produces_nothing(self, make_fixture):
        assert _rule_ledger_row(make_fixture(), _stored()) is None


class TestBanking:
    def test_the_rule_series_is_banked_beside_ours(self, engine, make_fixture, seed_fixtures):
        fixture = seed_fixtures(
            make_fixture(
                home_score=2,
                away_score=0,
                actual_result="home_win",
                odds_home=1.9,
                best_bet="home_win",
                best_bet_odds=1.9,
                best_bet_prob=0.55,
            )
        )[0]

        with Session(engine) as db:
            db.add(_stored(fixture_id=fixture.fixture_id))
            db.commit()

            written = bank_ledger(db, [db.get(type(fixture), fixture.id)])
            banked = db.exec(
                select(StatPitchSettledBet).where(
                    StatPitchSettledBet.fixture_id == fixture.fixture_id
                )
            ).all()

        assert written >= 2
        by_basis = {row.basis: row for row in banked}
        assert "1x2" in by_basis
        assert "rule" in by_basis
        # Ours took the averaged price; theirs took their own quote.
        assert by_basis["1x2"].odds_taken == 1.9
        assert by_basis["rule"].odds_taken == 1.75

    def test_banking_twice_writes_one_rule_row(self, engine, make_fixture, seed_fixtures):
        """The sync is idempotent, and the ledger holds one row per basis."""
        fixture = seed_fixtures(make_fixture(home_score=1, away_score=0, actual_result="home_win"))[
            0
        ]

        with Session(engine) as db:
            db.add(_stored(fixture_id=fixture.fixture_id))
            db.commit()

            row = db.get(type(fixture), fixture.id)
            bank_ledger(db, [row])
            row.ledgered = False
            db.add(row)
            db.commit()
            bank_ledger(db, [db.get(type(fixture), fixture.id)])

            rule_rows = db.exec(
                select(StatPitchSettledBet).where(
                    StatPitchSettledBet.fixture_id == fixture.fixture_id,
                    StatPitchSettledBet.basis == "rule",
                )
            ).all()

        assert len(rule_rows) == 1

    def test_a_fixture_with_no_staked_selection_still_marks_banked(
        self, engine, make_fixture, seed_fixtures
    ):
        """It owes the ledger nothing; leaving it unmarked would block pruning
        forever."""
        fixture = seed_fixtures(make_fixture(home_score=1, away_score=1, actual_result="draw"))[0]

        with Session(engine) as db:
            row = db.get(type(fixture), fixture.id)
            bank_ledger(db, [row])
            assert db.get(type(fixture), fixture.id).ledgered

    def test_todays_local_day_is_what_the_ledger_records(self, make_fixture):
        row = _rule_ledger_row(
            make_fixture(match_date=today_local(), home_score=1, away_score=0), _stored()
        )

        assert row.match_date == today_local()

"""Parsing StatPitch's priced card, and the guards around what a price means.

Every payload in `tests/fixtures/statpitch` is a real capture from the live
service (config `dec-2026.08.1-experimental`, schema_version 1, 2026-09-01),
trimmed only in row count. Testing against invented JSON would test our idea of
the contract rather than the contract, which is the mistake these captures
exist to prevent — the published contract and the deployed service disagree in
several places, and the captures are the half that is true.
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from api.statpitch.models import (
    SPBetsToday,
    SPCard,
    SPMatchdayOdds,
    SPSelection,
    StatPitchSelection,
)
from api.statpitch.selections import (
    BANKABLE_FAMILIES,
    OURS_TO_STATPITCH,
    STATPITCH_TO_OURS,
    is_bankable,
    translate,
)

FIXTURES = Path(__file__).parent / "fixtures" / "statpitch"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(name="card")
def card_fixture() -> SPCard:
    return SPCard.model_validate(_load("card_upcoming"))


@pytest.fixture(name="bet")
def bet_fixture(card: SPCard) -> SPSelection:
    return card.bets[0]


class TestSelectionRow:
    def test_the_captured_bet_parses(self, bet: SPSelection):
        assert bet.fixture_id == "ENG.PL|2026-2027|Arsenal FC|Chelsea FC"
        assert bet.selection == "1x2_home"
        assert bet.market_family == "1x2"
        assert bet.description == "Home win"

    def test_the_four_prices_stay_distinct(self, bet: SPSelection):
        """The whole contract turns on not collapsing these into one number."""
        assert bet.reference_odds == 1.68
        assert bet.consensus_odds == pytest.approx(1.6522, abs=1e-4)
        assert bet.odds == 1.75
        assert bet.fair_odds == 1.7295
        assert len({bet.reference_odds, bet.consensus_odds, bet.odds, bet.fair_odds}) == 4

    def test_p_used_equals_q_fair_and_model_edge_is_zero(self, bet: SPSelection):
        """The market-shrinkage weight fits at 0.000, so the model is not picking.

        If this ever fails, the model has started contributing and a `rule` row
        can no longer be described as a pure price disagreement.
        """
        assert bet.p_used == bet.q_fair
        assert bet.p_used != bet.p_model
        assert bet.model_edge == 0.0

    def test_a_bet_is_recommended_and_an_assessment_is_not(self, card: SPCard):
        assert card.bets[0].recommended
        unstaked = [a for a in card.assessments if a.stake_fraction == 0.0]
        assert unstaked, "the capture should carry assessments that were not staked"
        assert not any(a.recommended for a in unstaked)

    def test_the_unpublished_contract_fields_are_none_not_guessed(self, bet: SPSelection):
        """`selection_basis`, `pricing` and `model_odds` are not live yet.

        They must read as absent rather than be inferred. A guessed `pricing`
        is the one field capable of presenting our own opinion as a price a
        bookmaker offered.
        """
        assert bet.selection_basis is None
        assert bet.pricing is None
        assert bet.model_odds is None

    def test_a_refused_selection_says_why(self):
        odds = SPMatchdayOdds.model_validate(_load("odds_matchday"))
        row = odds.fixtures[0].markets["1x2"][0]
        assert row.stake_fraction == 0.0
        assert row.reasons
        assert any("no benchmark" in reason for reason in row.reasons)


class TestBetsToday:
    def test_an_empty_day_parses_with_its_reason(self):
        bets = SPBetsToday.model_validate(_load("bets_today"))
        assert bets.for_date == date(2026, 9, 1)
        assert bets.count == 0
        assert bets.bets == []
        assert bets.empty_because is not None
        assert bets.empty_because.cause == "fixtures_today_carry_no_price"

    def test_the_refusal_is_advisory_and_the_caveat_survives(self):
        """This refusal is not a failure.

        `SELECTION_RULE_EXPERIMENTAL` is a standing statement about the rule's
        calibration, present on every response while the rule is experimental,
        and the card returns real bets beside it. Treating it the way the
        fixtures refusal is treated would fail every sync.
        """
        bets = SPBetsToday.model_validate(_load("bets_today"))
        assert bets.refusal is not None
        assert bets.refusal.available is False
        assert bets.refusal.reason_code == "SELECTION_RULE_EXPERIMENTAL"
        assert bets.caveat
        assert "experimental" in bets.caveat

    def test_the_selection_rule_is_captured_including_its_reference(self):
        """The benchmark book is not a constant and has to be stored per row."""
        rule = SPBetsToday.model_validate(_load("bets_today")).selection_rule
        assert rule.status == "experimental"
        assert rule.reference == "odds_pinnacle"
        assert rule.market_families == ["1x2"]
        assert rule.max_per_day == 3

    def test_by_basis_and_confidence_caveat_are_absent_upstream(self):
        bets = SPBetsToday.model_validate(_load("bets_today"))
        assert bets.by_basis == {}
        assert bets.confidence_caveat is None


class TestCard:
    def test_the_window_aliases_parse(self, card: SPCard):
        """`from` is a Python keyword, so both ends are aliased."""
        assert card.from_date == date(2026, 9, 1)
        assert card.to_date == date(2026, 9, 15)

    def test_bets_and_assessments_share_one_shape(self, card: SPCard):
        assert all(isinstance(row, SPSelection) for row in card.bets + card.assessments)

    def test_the_card_reaches_past_today(self, card: SPCard):
        """Why `/card/today` is never the sync source.

        Prices publish days ahead, so the today-filtered variant returns
        nothing while a full slate sits in the card behind it.
        """
        assert card.dates_covered
        assert min(card.dates_covered) > card.from_date


class TestMatchdayOdds:
    def test_kickoff_is_parsed_as_an_aware_utc_instant(self):
        """The only real instant available, now that we fetch no bookmaker odds.

        It arrives as "2026-09-02 18:45:00" — no offset, but documented UTC by
        its own name. Every local-day bucket depends on it.
        """
        odds = SPMatchdayOdds.model_validate(_load("odds_matchday"))
        kickoff = odds.fixtures[0].kickoff_utc
        assert kickoff == datetime(2026, 9, 2, 18, 45, tzinfo=UTC)
        assert kickoff.tzinfo is not None

    def test_markets_are_grouped_by_family(self):
        odds = SPMatchdayOdds.model_validate(_load("odds_matchday"))
        fixture = odds.fixtures[0]
        assert fixture.markets_priced == ["1x2"]
        assert sorted(row.selection for row in fixture.markets["1x2"]) == [
            "1x2_away",
            "1x2_draw",
            "1x2_home",
        ]


class TestTranslation:
    def test_the_1x2_family_maps_onto_our_names(self):
        assert translate("1x2_home") == "home_win"
        assert translate("1x2_draw") == "draw"
        assert translate("1x2_away") == "away_win"

    def test_an_unknown_selection_is_refused_rather_than_guessed(self):
        """Safe direction: untranslatable means displayed, never staked."""
        assert translate("totals_over") is None
        assert translate("") is None

    def test_the_map_round_trips(self):
        for theirs, ours in STATPITCH_TO_OURS.items():
            assert OURS_TO_STATPITCH[ours] == theirs

    def test_our_names_are_the_ones_the_ledger_already_settles(self):
        """These cannot drift: the ledger holds settled bets under them."""
        from api.statpitch.settlement import selection_won

        for ours in STATPITCH_TO_OURS.values():
            selection_won(ours, 1, 0)

    def test_only_1x2_is_bankable(self):
        """`selection_won` resolves every market it knows on halves, so nothing
        can push — but a handicap can, and would settle a push as a loss."""
        assert is_bankable("1x2")
        assert not is_bankable("handicap")
        assert not is_bankable("totals")
        assert BANKABLE_FAMILIES == {"1x2"}


class TestStoredSelection:
    def test_a_real_price_is_bettable(self):
        row = StatPitchSelection(
            fixture_id="x", selection="1x2_home", market_family="1x2", odds=1.75
        )
        assert row.bettable

    def test_a_model_priced_row_is_never_bettable(self):
        """Its `odds` is 1 / p_model — our own opinion wearing a price's clothes."""
        row = StatPitchSelection(
            fixture_id="x",
            selection="1x2_home",
            market_family="1x2",
            odds=1.3663,
            pricing="model",
        )
        assert not row.bettable

    def test_a_missing_or_impossible_price_is_not_bettable(self):
        for odds in (None, 1.0, 0.0):
            row = StatPitchSelection(
                fixture_id="x", selection="1x2_home", market_family="1x2", odds=odds
            )
            assert not row.bettable

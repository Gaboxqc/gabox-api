"""Settling fixtures, banking the ledger, and pruning the cache.

The ordering here is the whole point. Fixtures live three days and are then
deleted, but ROI is measured over seven and thirty — so a fixture's result must
be written to the permanent ledger *before* it becomes eligible for pruning.
`prune_fixtures` refuses to drop a fixture that still owes the ledger a row,
which makes the retention policy and the track record independent by
construction rather than by scheduling luck.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from api.statpitch.clock import Window, to_local_date, today_local
from api.statpitch.matching import best_match
from api.statpitch.models import StatPitchFixture, StatPitchSettledBet
from api.statpitch.pricing import odds_of, probability_of
from api.statpitch.scores_service import MatchScore

log = logging.getLogger("statpitch.settlement")

# A fixture with no result after this long is dropped anyway. Results can go
# missing — a postponement, a gap in the scores feed — and without a backstop
# those rows would sit in a three-day cache forever.
_ABANDON_AFTER_DAYS = 14


@dataclass
class SettlementResult:
    settled: int = 0
    ledgered: int = 0
    pruned: int = 0
    abandoned: int = 0
    warnings: list[str] = field(default_factory=list)


def selection_won(selection: str, home_score: int, away_score: int) -> bool:
    """Did this selection win, given the final score?

    Every market StatPitch prices resolves cleanly from the scoreline — the
    lines are all halves, so nothing can push.
    """
    total = home_score + away_score
    both_scored = home_score > 0 and away_score > 0

    match selection:
        case "home_win":
            return home_score > away_score
        case "draw":
            return home_score == away_score
        case "away_win":
            return home_score < away_score
        case "over_1_5":
            return total > 1.5
        case "under_1_5":
            return total < 1.5
        case "over_2_5":
            return total > 2.5
        case "under_2_5":
            return total < 2.5
        case "over_3_5":
            return total > 3.5
        case "under_3_5":
            return total < 3.5
        case "btts_yes":
            return both_scored
        case "btts_no":
            return not both_scored
        case _:
            raise ValueError(f"Cannot settle unknown selection {selection!r}")


def apply_scores(
    session: Session, fixtures: list[StatPitchFixture], scores: list[MatchScore]
) -> int:
    """Attach final scores to unsettled fixtures. Returns how many settled."""
    completed = [
        score
        for score in scores
        if score.completed and score.home_score is not None and score.away_score is not None
    ]
    if not completed:
        return 0

    settled = 0
    for fixture in fixtures:
        if fixture.actual_result is not None:
            continue

        in_competition = [
            score for score in completed if score.competition_id == fixture.competition_id
        ]
        match = best_match(
            fixture.home_team, fixture.away_team, in_competition, key=lambda s: s.teams
        )
        if match is None:
            continue

        score, _ = match
        # A club can meet the same opponent twice in a window (a cup replay, or
        # a rescheduled tie), so the scoreline has to land on the right leg.
        if abs((to_local_date(score.commence_time) - fixture.match_date).days) > 1:
            continue

        fixture.home_score = score.home_score
        fixture.away_score = score.away_score
        fixture.actual_result = score.actual_result
        fixture.settled_at = datetime.now(UTC)
        session.add(fixture)
        settled += 1
        log.info(
            "Settled %s vs %s -> %d-%d (%s)",
            fixture.home_team,
            fixture.away_team,
            score.home_score,
            score.away_score,
            score.actual_result,
        )

    if settled:
        session.commit()
    return settled


def _ledger_row(
    fixture: StatPitchFixture, basis: str, selection: str
) -> StatPitchSettledBet | None:
    odds = odds_of(fixture, selection)
    probability = probability_of(fixture, selection)
    if odds is None or probability is None or fixture.home_score is None:
        return None
    if fixture.away_score is None:
        return None

    won = selection_won(selection, fixture.home_score, fixture.away_score)
    stake = 1.0
    # Flat stake, so ROI reads as return per unit and the two series stay
    # comparable. The Kelly recommendation rides along unbaked, leaving a
    # stake-weighted ROI derivable later without rewriting settled history.
    kelly = fixture.best_overall_kelly if basis == "overall" else None

    return StatPitchSettledBet(
        fixture_id=fixture.fixture_id,
        competition_id=fixture.competition_id,
        home_team=fixture.home_team,
        away_team=fixture.away_team,
        match_date=fixture.match_date,
        basis=basis,
        selection=selection,
        probability=probability,
        odds_taken=odds,
        stake_units=stake,
        kelly_fraction=kelly,
        won=won,
        pnl_units=round((odds - 1) * stake if won else -stake, 4),
        home_score=fixture.home_score,
        away_score=fixture.away_score,
        model_version=fixture.model_version,
    )


def bank_ledger(session: Session, fixtures: list[StatPitchFixture]) -> int:
    """Write ledger rows for settled fixtures, then mark them banked.

    A fixture with a result but no qualifying pick is still marked `ledgered`:
    it owes the ledger nothing, and leaving it unmarked would block pruning
    forever.
    """
    written = 0

    for fixture in fixtures:
        if fixture.ledgered or fixture.actual_result is None:
            continue

        existing = set(
            session.exec(
                select(StatPitchSettledBet.basis).where(
                    StatPitchSettledBet.fixture_id == fixture.fixture_id
                )
            ).all()
        )

        for basis, selection in (
            ("1x2", fixture.best_bet),
            ("overall", fixture.best_overall_bet),
        ):
            if selection is None or basis in existing:
                continue
            row = _ledger_row(fixture, basis, selection)
            if row is None:
                continue
            session.add(row)
            written += 1

        fixture.ledgered = True
        session.add(fixture)

    if written or fixtures:
        session.commit()
    return written


def prune_fixtures(session: Session, window: Window) -> tuple[int, int, list[str]]:
    """Delete fixtures outside the three-day window.

    Returns (pruned, abandoned, warnings). A fixture is only removed once its
    result is banked, so nothing leaves the cache before the ledger has it.
    """
    warnings: list[str] = []
    stale = session.exec(
        select(StatPitchFixture).where(
            (StatPitchFixture.match_date < window.start)
            | (StatPitchFixture.match_date > window.end)
        )
    ).all()

    if not stale:
        return 0, 0, warnings

    cutoff = today_local() - timedelta(days=_ABANDON_AFTER_DAYS)
    pruned = 0
    abandoned = 0

    for fixture in stale:
        # Never drop a future fixture that drifted out of the window; the next
        # sync will simply re-file it under its new date.
        if fixture.match_date > window.end:
            session.delete(fixture)
            pruned += 1
            continue

        if fixture.ledgered:
            session.delete(fixture)
            pruned += 1
        elif fixture.match_date < cutoff:
            session.delete(fixture)
            abandoned += 1
            warnings.append(
                f"Dropped {fixture.home_team} vs {fixture.away_team} "
                f"({fixture.match_date}) with no result after {_ABANDON_AFTER_DAYS} days."
            )
        # Otherwise leave it: it is past the window but still waiting on a
        # result, and deleting it now would lose the bet from the record.

    session.commit()

    if pruned or abandoned:
        log.info("Pruned %d fixture(s), abandoned %d without a result.", pruned, abandoned)
    return pruned, abandoned, warnings

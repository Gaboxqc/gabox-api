"""The daily StatPitch sync.

One pass does the whole job, in an order that matters:

    fetch fixtures -> price them -> settle finished ones -> bank the ledger -> prune

Settling and banking come before pruning so a result is never lost to the
three-day retention. Everything is idempotent and keyed on `fixture_id`, so
running it twice in a row changes nothing and a failed run is fixed by the next
one rather than by hand.

There is no in-process scheduler: the app runs serverless, where background
threads do not survive between requests. The rollover is driven externally by
hitting `POST /statpitch/sync` at 06:00 UTC, which is midnight in Nicaragua.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlmodel import Session, select

from api.core.config import settings
from api.statpitch.client import (
    StatPitchError,
    build_client,
    fetch_fixture_window,
    fetch_health,
)
from api.statpitch.clock import Window, current_window, to_local_date
from api.statpitch.leagues import STATPITCH_ODDS_COVERAGE
from api.statpitch.matching import best_match
from api.statpitch.models import SPFixture, StatPitchFixture
from api.statpitch.odds_service import OddsEvent, OddsUnavailable, fetch_odds
from api.statpitch.pricing import apply_pricing
from api.statpitch.scores_service import fetch_scores
from api.statpitch.settlement import apply_scores, bank_ledger, prune_fixtures

log = logging.getLogger("statpitch.sync")


@dataclass
class SyncReport:
    window: Window
    fetched: int = 0
    stored: int = 0
    priced: int = 0
    unmatched_odds: int = 0
    settled: int = 0
    ledgered: int = 0
    pruned: int = 0
    model_version: str | None = None
    warnings: list[str] = field(default_factory=list)


def configured_competitions() -> set[str]:
    return {c.strip() for c in settings.statpitch_competitions if c.strip()}


def _to_row(fixture: SPFixture, config_version: str | None) -> StatPitchFixture | None:
    """Map a StatPitch fixture onto a database row.

    Returns None when the prediction is absent — a fixture with no numbers has
    nothing to show and nothing to price.
    """
    prediction = fixture.prediction
    if prediction is None:
        return None

    return StatPitchFixture(
        fixture_id=fixture.fixture_id,
        competition_id=fixture.competition_id,
        season=fixture.season,
        stage=fixture.stage,
        format=fixture.format,
        # Overwritten below once an odds event supplies a real instant.
        match_date=fixture.date,
        source_date=fixture.date,
        kickoff=fixture.kickoff,
        date_confirmed=fixture.date_confirmed,
        home_team=fixture.home_team,
        away_team=fixture.away_team,
        neutral_venue=fixture.neutral_venue,
        odds_coverage=fixture.odds_coverage,
        prediction_source=fixture.prediction_source,
        model_version=fixture.prediction_model_version or "unknown",
        config_version=config_version,
        fully_rated=prediction.fully_rated,
        home_xg=prediction.expected_goals.home,
        away_xg=prediction.expected_goals.away,
        home_elo=prediction.ratings.home.elo,
        away_elo=prediction.ratings.away.elo,
        home_elo_source=prediction.ratings.home.source,
        away_elo_source=prediction.ratings.away.source,
        home_win_prob=prediction.probabilities.home,
        draw_prob=prediction.probabilities.draw,
        away_win_prob=prediction.probabilities.away,
        over_1_5=prediction.over_under.over_1_5,
        over_2_5=prediction.over_under.over_2_5,
        over_3_5=prediction.over_under.over_3_5,
        btts_yes=prediction.btts,
        # StatPitch publishes P(both score) as a single float; the complement
        # is the only other outcome.
        btts_no=round(1 - prediction.btts, 6),
        correct_scores=[score.model_dump() for score in prediction.correct_scores] or None,
        explanation=fixture.explanation,
    )


def _attach_odds(row: StatPitchFixture, event: OddsEvent) -> None:
    row.commence_time = event.commence_time
    # A real instant beats StatPitch's nominal date: `kickoff` is a bare
    # "20:00" with no zone, which cannot be converted to a local day at all.
    row.match_date = to_local_date(event.commence_time)
    row.odds_home = event.odds_home
    row.odds_draw = event.odds_draw
    row.odds_away = event.odds_away
    row.odds_over_1_5 = event.odds_over_1_5
    row.odds_under_1_5 = event.odds_under_1_5
    row.odds_over_2_5 = event.odds_over_2_5
    row.odds_under_2_5 = event.odds_under_2_5
    row.odds_over_3_5 = event.odds_over_3_5
    row.odds_under_3_5 = event.odds_under_3_5
    row.odds_btts_yes = event.odds_btts_yes
    row.odds_btts_no = event.odds_btts_no


# Fields the sync refreshes on an existing row. Everything absent from this
# list is either identity or settlement state, and must survive a re-sync:
# overwriting `actual_result` or `ledgered` would resurrect a banked bet.
_REFRESHABLE = (
    "competition_id",
    "season",
    "stage",
    "format",
    "match_date",
    "source_date",
    "kickoff",
    "commence_time",
    "date_confirmed",
    "home_team",
    "away_team",
    "neutral_venue",
    "odds_coverage",
    "prediction_source",
    "model_version",
    "config_version",
    "fully_rated",
    "home_xg",
    "away_xg",
    "home_elo",
    "away_elo",
    "home_elo_source",
    "away_elo_source",
    "home_win_prob",
    "draw_prob",
    "away_win_prob",
    "over_1_5",
    "over_2_5",
    "over_3_5",
    "btts_yes",
    "btts_no",
    "correct_scores",
    "explanation",
    "odds_home",
    "odds_draw",
    "odds_away",
    "odds_over_1_5",
    "odds_under_1_5",
    "odds_over_2_5",
    "odds_under_2_5",
    "odds_over_3_5",
    "odds_under_3_5",
    "odds_btts_yes",
    "odds_btts_no",
    "ev_home",
    "ev_draw",
    "ev_away",
    "ev_over_1_5",
    "ev_under_1_5",
    "ev_over_2_5",
    "ev_under_2_5",
    "ev_over_3_5",
    "ev_under_3_5",
    "ev_btts_yes",
    "ev_btts_no",
    "kelly_home",
    "kelly_draw",
    "kelly_away",
    "kelly_over_1_5",
    "kelly_under_1_5",
    "kelly_over_2_5",
    "kelly_under_2_5",
    "kelly_over_3_5",
    "kelly_under_3_5",
    "kelly_btts_yes",
    "kelly_btts_no",
    "best_bet",
    "best_bet_odds",
    "best_bet_prob",
    "best_overall_bet",
    "best_overall_odds",
    "best_overall_prob",
    "best_overall_ev",
    "best_overall_kelly",
)


def _upsert(session: Session, incoming: list[StatPitchFixture]) -> int:
    """Insert or refresh rows, keyed on `fixture_id`.

    `fixture_id` excludes the date on purpose, so a postponed match updates in
    place instead of appearing as a new fixture plus a vanished one.
    """
    if not incoming:
        return 0

    ids = [row.fixture_id for row in incoming]
    existing = {
        row.fixture_id: row
        for row in session.exec(
            select(StatPitchFixture).where(StatPitchFixture.fixture_id.in_(ids))
        ).all()
    }

    for row in incoming:
        current = existing.get(row.fixture_id)
        if current is None:
            session.add(row)
            continue

        # A settled fixture keeps the price it was settled at; re-pricing it
        # would silently rewrite the bet the ledger already recorded.
        if current.ledgered:
            continue

        for name in _REFRESHABLE:
            setattr(current, name, getattr(row, name))
        current.synced_at = datetime.now(UTC)
        session.add(current)

    session.commit()
    return len(incoming)


async def run_sync(session: Session) -> SyncReport:
    """Fetch, price, settle, bank and prune. Safe to run repeatedly."""
    window = current_window()
    report = SyncReport(window=window)
    competitions = configured_competitions()

    if not competitions:
        report.warnings.append("No competitions configured; nothing to sync.")
        return report

    uncovered = competitions - STATPITCH_ODDS_COVERAGE
    if uncovered:
        report.warnings.append(
            "StatPitch reports no odds source for "
            f"{', '.join(sorted(uncovered))}; those fixtures are priced only if "
            "The Odds API covers them."
        )

    # ── 1. Fixtures and predictions ──────────────────────────────────────────
    async with build_client() as client:
        health = await fetch_health(client)
        if not health.ready:
            raise StatPitchError(
                f"StatPitch is not ready (status={health.status}): "
                f"{health.error or 'artifacts still loading'}"
            )

        fetched = await fetch_fixture_window(client, window.start, window.end, competitions)

    report.fetched = len(fetched.fixtures)
    report.model_version = fetched.model_version
    report.warnings.extend(fetched.warnings)

    rows = [row for row in (_to_row(f, fetched.config_version) for f in fetched.fixtures) if row]

    # ── 2. Prices ────────────────────────────────────────────────────────────
    try:
        odds = await fetch_odds(competitions)
        report.warnings.extend(odds.warnings)
    except OddsUnavailable as exc:
        # Predictions are still worth storing and showing without a price; only
        # the betting half of the product is lost.
        odds = None
        report.warnings.append(f"No odds this run: {exc}")

    if odds is not None:
        by_competition: dict[str, list[OddsEvent]] = {}
        for event in odds.events:
            by_competition.setdefault(event.competition_id, []).append(event)

        for row in rows:
            candidates = by_competition.get(row.competition_id, [])
            match = best_match(row.home_team, row.away_team, candidates, key=lambda e: e.teams)
            if match is None:
                report.unmatched_odds += 1
                continue
            _attach_odds(row, match[0])
            report.priced += 1

    for row in rows:
        apply_pricing(row)

    report.stored = _upsert(session, rows)

    # ── 3. Results ───────────────────────────────────────────────────────────
    unsettled = session.exec(
        select(StatPitchFixture).where(StatPitchFixture.actual_result.is_(None))
    ).all()

    if unsettled:
        try:
            scores = await fetch_scores(competitions, days_back=3)
            report.warnings.extend(scores.warnings)
            report.settled = apply_scores(session, unsettled, scores.scores)
        except OddsUnavailable as exc:
            report.warnings.append(f"No scores this run: {exc}")

    # ── 4. Ledger, then prune ────────────────────────────────────────────────
    settled = session.exec(
        select(StatPitchFixture).where(
            StatPitchFixture.actual_result.is_not(None),
            StatPitchFixture.ledgered.is_(False),
        )
    ).all()
    report.ledgered = bank_ledger(session, settled)

    pruned, abandoned, prune_warnings = prune_fixtures(session, window)
    report.pruned = pruned + abandoned
    report.warnings.extend(prune_warnings)

    log.info(
        "Sync complete: fetched=%d stored=%d priced=%d settled=%d ledgered=%d pruned=%d",
        report.fetched,
        report.stored,
        report.priced,
        report.settled,
        report.ledgered,
        report.pruned,
    )
    return report

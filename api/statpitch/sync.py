"""The daily StatPitch sync.

One pass does the whole job, in an order that matters:

    fetch fixtures and prices -> store them -> settle finished ones
    -> bank the ledger -> prune

Settling and banking come before pruning so a result is never lost to the
three-day retention. Everything is idempotent and keyed on `fixture_id`, so
running it twice in a row changes nothing and a failed run is fixed by the next
one rather than by hand.

Prices come from StatPitch now, not The Odds API. It publishes its own card
against a 25-book panel, keyed by `fixture_id`, so a price is joined by identity
rather than by matching two spellings of a club's name — and at no quota cost.
Results come from ESPN, keyed by the same league slug the crests use. StatPitch
has no results endpoint — its own ledger carries `result: null` — so without an
external source nothing settles and there is no ROI at all. Nothing in this
module needs an API key any more.

Four upstream calls make one pass, in one client, because the free instance
sleeps after fifteen minutes and the cold start is worth paying once:
`/fixtures/upcoming` for predictions, `/card/upcoming` for every priced and
graded selection, `/bets/today` for the daily pick and the caveats that qualify
it, and `/odds/matchday` per window day — the only place `kickoff_utc` is
published, and therefore the only source of the real instant every local-day
bucket is computed from.

There is no in-process scheduler: the app runs serverless, where background
threads do not survive between requests. The rollover is driven externally by
hitting `POST /statpitch/sync` at 06:00 UTC, which is midnight in Nicaragua.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlmodel import Session, col, select

from api.core.config import settings
from api.statpitch.client import (
    StatPitchError,
    build_client,
    fetch_bets_today,
    fetch_card,
    fetch_fixture_window,
    fetch_health,
    fetch_matchday_odds,
)
from api.statpitch.clock import Window, current_window, to_local_date
from api.statpitch.leagues import PRICED_LEAGUES
from api.statpitch.models import (
    SPBetsToday,
    SPFixture,
    SPSelection,
    StatPitchBetDay,
    StatPitchFixture,
    StatPitchSelection,
)
from api.statpitch.motd import ensure as ensure_match_of_the_day
from api.statpitch.pricing import apply_pricing, market_for
from api.statpitch.scores_service import fetch_scores
from api.statpitch.selections import translate
from api.statpitch.settlement import apply_scores, bank_ledger, prune_fixtures
from api.statpitch.teams import link_fixtures

log = logging.getLogger("statpitch.sync")


@dataclass
class SyncReport:
    window: Window
    fetched: int = 0
    stored: int = 0
    priced: int = 0
    # Fixtures StatPitch published no price for. Not a matching failure any
    # more — prices arrive keyed by `fixture_id`, so there is nothing left to
    # mismatch. A fixture is unpriced because the feed has not published its
    # matchday block yet, which is normal days ahead of kickoff.
    unpriced: int = 0
    # StatPitch selection rows stored, and how many of them it actually staked.
    selections: int = 0
    rule_bets: int = 0
    settled: int = 0
    ledgered: int = 0
    pruned: int = 0
    clubs: int = 0
    # Whatever was picked, or already stood, for today.
    match_of_the_day: str | None = None
    # Sides rendering without a crest. Non-fatal — the UI falls back to a
    # monogram — but a jump here means the registry needs a backfill run.
    missing_crests: int = 0
    model_version: str | None = None
    warnings: list[str] = field(default_factory=list)


def configured_competitions() -> set[str]:
    return {c.strip() for c in settings.statpitch_competitions if c.strip()}


def _to_row(
    fixture: SPFixture, config_version: str | None
) -> tuple[StatPitchFixture, str, str] | None:
    """Map a StatPitch fixture onto a database row and its two club names.

    The names travel beside the row rather than on it: clubs are a foreign key
    now, and the id cannot be assigned until the registry has been consulted.

    Returns None when the prediction is absent — a fixture with no numbers has
    nothing to show and nothing to price.
    """
    prediction = fixture.prediction
    if prediction is None:
        return None

    row = StatPitchFixture(
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
    return row, fixture.home_team, fixture.away_team


def _is_bettable(selection: SPSelection) -> bool:
    """Whether this row's `odds` is a price somebody is actually offering.

    Three of the four prices on a selection are opinions — `fair_odds` and
    `model_odds` are ours and StatPitch's, `reference_odds` is a benchmark —
    and only `odds`, the best quote, can be taken. When `pricing` ships
    upstream, a `model` row is excluded here too: its `odds` is `1 / p_model`,
    which is an opinion wearing a price's clothes and must never reach a column
    the ledger later reads as a price taken.
    """
    if selection.odds is None or selection.odds <= 1:
        return False
    return selection.pricing != "model"


def _attach_kickoff(row: StatPitchFixture, kickoff: datetime) -> None:
    """Give the fixture a real instant, and file it under the right local day.

    This is the one thing StatPitch's fixture feed cannot supply: its `kickoff`
    is a bare "20:00" with no zone, which cannot be converted to a local day at
    all. `/odds/matchday` publishes `kickoff_utc`, which is why that endpoint is
    called even on a day whose prices we would otherwise not need.
    """
    row.commence_time = kickoff
    row.match_date = to_local_date(kickoff)


def _attach_odds(row: StatPitchFixture, selections: list[SPSelection]) -> bool:
    """Copy StatPitch's best quotes onto the fixture's own price columns.

    Only bettable rows, and only selections we have a name for. The column is
    resolved through `pricing.MARKETS` rather than a second mapping table, so
    a market added there is priced here without a matching edit.

    Returns whether anything was priced.
    """
    priced = False

    for selection in selections:
        if not _is_bettable(selection):
            continue
        ours = translate(selection.selection)
        if ours is None:
            continue
        market = market_for(ours)
        if market is None:
            continue
        setattr(row, market.odds_field, selection.odds)
        priced = True

    return priced


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
    "home_team_id",
    "away_team_id",
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


def _selection_row(
    selection: SPSelection,
    *,
    captured_at: datetime | None,
    config_status: str | None,
    rule_status: str | None,
    rule_reference: str | None,
) -> StatPitchSelection:
    """One StatPitch selection, stored as it arrived.

    The provenance arguments ride onto every row rather than being recorded
    once per sync. That is the contract's own instruction, and the reason is
    concrete: when the selection rule is promoted from `experimental` to
    `fitted`, a row banked today must still read as experimental. A status
    joined at read time would silently relabel history.
    """
    return StatPitchSelection(
        fixture_id=selection.fixture_id,
        selection=selection.selection,
        our_selection=translate(selection.selection),
        market_family=selection.market_family,
        line=selection.line,
        description=selection.description,
        reference_odds=selection.reference_odds,
        consensus_odds=selection.consensus_odds,
        odds=selection.odds,
        fair_odds=selection.fair_odds,
        p_model=selection.p_model,
        q_fair=selection.q_fair,
        p_used=selection.p_used,
        edge_prob=selection.edge_prob,
        expected_value=selection.expected_value,
        price_edge=selection.price_edge,
        model_edge=selection.model_edge,
        rule_edge=selection.rule_edge,
        rule_qualified=selection.rule_qualified,
        grade=selection.grade,
        composite=selection.composite,
        stake_fraction=selection.stake_fraction,
        reasons=selection.reasons or None,
        config_status=config_status,
        selection_rule_status=rule_status,
        selection_rule_reference=rule_reference,
        selection_basis=selection.selection_basis,
        pricing=selection.pricing,
        model_odds=selection.model_odds,
        captured_at=captured_at,
    )


# Everything a re-sync refreshes on an existing selection. `fixture_id` and
# `selection` are the identity and are absent on purpose.
_REFRESHABLE_SELECTION = (
    "our_selection",
    "market_family",
    "line",
    "description",
    "reference_odds",
    "consensus_odds",
    "odds",
    "fair_odds",
    "p_model",
    "q_fair",
    "p_used",
    "edge_prob",
    "expected_value",
    "price_edge",
    "model_edge",
    "rule_edge",
    "rule_qualified",
    "grade",
    "composite",
    "stake_fraction",
    "reasons",
    "config_status",
    "selection_rule_status",
    "selection_rule_reference",
    "selection_basis",
    "pricing",
    "model_odds",
    "captured_at",
)


def _upsert_selections(
    session: Session, incoming: list[StatPitchSelection], ledgered: set[str]
) -> int:
    """Insert or refresh selections, keyed on `(fixture_id, selection)`.

    A selection belonging to a fixture the ledger has already banked is left
    alone, exactly as `_upsert` leaves the fixture alone. Re-pricing a settled
    bet would rewrite the price its result was recorded at.
    """
    if not incoming:
        return 0

    fixture_ids = {row.fixture_id for row in incoming}
    existing = {
        (row.fixture_id, row.selection): row
        for row in session.exec(
            select(StatPitchSelection).where(col(StatPitchSelection.fixture_id).in_(fixture_ids))
        ).all()
    }

    stored = 0
    for row in incoming:
        if row.fixture_id in ledgered:
            continue

        current = existing.get((row.fixture_id, row.selection))
        if current is None:
            session.add(row)
            stored += 1
            continue

        for name in _REFRESHABLE_SELECTION:
            setattr(current, name, getattr(row, name))
        current.synced_at = datetime.now(UTC)
        session.add(current)
        stored += 1

    session.commit()
    return stored


def _upsert_bet_day(session: Session, day: date, payload: SPBetsToday) -> None:
    """Cache what `/bets/today` said, caveats included.

    The caveats are the reason this is stored rather than proxied. They are the
    difference between a pick a reader can weigh and a pick they cannot, and
    the upstream instance sleeps after fifteen minutes idle — so proxying would
    drop them at exactly the moment a reader is looking at a bet.
    """
    rule = payload.selection_rule
    refusal = payload.refusal

    row = session.exec(
        select(StatPitchBetDay).where(StatPitchBetDay.match_date == day)
    ).first() or StatPitchBetDay(match_date=day)

    row.count = payload.count
    row.assessed = payload.assessed
    row.qualified_by_rule = payload.qualified_by_rule
    row.total_exposure = payload.total_exposure
    row.caveat = payload.caveat
    row.confidence_caveat = payload.confidence_caveat
    row.disclaimer = payload.disclaimer
    row.reason = payload.reason
    row.binding_constraint = payload.binding_constraint
    row.empty_because = (
        payload.empty_because.model_dump(mode="json") if payload.empty_because else None
    )
    row.config_status = payload.config_status or rule.status
    row.selection_rule_status = rule.status
    row.selection_rule = rule.model_dump(mode="json")
    # Lifted out of the blob because slice C filters on it. `or None` rather
    # than an empty list: upstream not publishing a scope and upstream
    # publishing an empty one would otherwise be indistinguishable, and only the
    # first is true today for an older config.
    row.selection_rule_competitions = list(rule.competitions) or None
    row.by_basis = payload.by_basis or None
    row.refusal_reason_code = refusal.reason_code if refusal else None
    row.refusal_reason = refusal.reason if refusal else None
    row.model_version = payload.model_version
    row.config_version = payload.config_version
    row.generated_at = payload.generated_at
    row.synced_at = datetime.now(UTC)

    session.add(row)
    session.commit()


async def run_sync(session: Session) -> SyncReport:
    """Fetch, price, settle, bank and prune. Safe to run repeatedly."""
    window = current_window()
    report = SyncReport(window=window)
    competitions = configured_competitions()

    if not competitions:
        report.warnings.append("No competitions configured; nothing to sync.")
        return report

    uncovered = competitions - PRICED_LEAGUES
    if uncovered:
        # Nothing else can price them now that The Odds API is gone, so these
        # fixtures store their prediction and stay unpriced for good.
        report.warnings.append(
            "StatPitch publishes no odds for "
            f"{', '.join(sorted(uncovered))}; those fixtures store a prediction "
            "but will never carry a price or a bet."
        )

    # ── 1. Everything StatPitch has to say ───────────────────────────────────
    # One client for the whole batch. The free instance sleeps after fifteen
    # minutes idle, so the cold start is worth paying once rather than per call.
    async with build_client() as client:
        health = await fetch_health(client)
        if not health.ready:
            raise StatPitchError(
                f"StatPitch is not ready (status={health.status}): "
                f"{health.error or 'artifacts still loading'}"
            )

        fetched = await fetch_fixture_window(client, window.start, window.end, competitions)
        # The card only reaches forward, so it can cover the window's forward
        # half and nothing else — yesterday is unreachable by construction.
        # Asking for more days than the cache retains would return selections
        # for fixtures we do not store, which are discarded on arrival.
        card = await fetch_card(client, (window.end - window.today).days)
        bets_today = await fetch_bets_today(client)

        # `kickoff_utc` is published only on `/odds/matchday`, and it is the
        # only real instant available anywhere — the fixture feed's `kickoff` is
        # a bare "20:00" with no zone. Every local-day bucket depends on it, so
        # this endpoint is called for its timestamps even on a day whose prices
        # the card already carries.
        kickoffs: dict[str, datetime] = {}
        for day in (window.yesterday, window.today, window.tomorrow):
            try:
                matchday = await fetch_matchday_odds(client, day)
            except StatPitchError as exc:
                report.warnings.append(f"No matchday prices for {day}: {exc}")
                continue
            for priced_fixture in matchday.fixtures:
                if priced_fixture.kickoff_utc is not None:
                    kickoffs[priced_fixture.fixture_id] = priced_fixture.kickoff_utc

    report.fetched = len(fetched.fixtures)
    report.model_version = fetched.model_version
    report.warnings.extend(fetched.warnings)

    resolved = [
        entry for entry in (_to_row(f, fetched.config_version) for f in fetched.fixtures) if entry
    ]
    rows = [row for row, _, _ in resolved]

    # ── 2. Prices ────────────────────────────────────────────────────────────
    # StatPitch prices its own card now, and the rows arrive keyed by
    # `fixture_id`. Nothing is matched by club name any more, so the whole class
    # of mismatch `matching` exists to prevent cannot happen to a price — it
    # still can to a *result*, which is why that module has not gone anywhere.
    #
    # `assessments` is everything priced and graded; `bets` is the subset
    # StatPitch staked. Both are stored, so a fixture page can show the market
    # on a match where nothing was recommended. `bets` is folded in second so a
    # staked row wins over its unstaked duplicate.
    by_fixture: dict[str, dict[str, SPSelection]] = {}
    for selection in card.assessments + card.bets:
        by_fixture.setdefault(selection.fixture_id, {})[selection.selection] = selection

    rule = bets_today.selection_rule
    incoming_selections: list[StatPitchSelection] = []

    for row in rows:
        kickoff = kickoffs.get(row.fixture_id)
        if kickoff is not None:
            _attach_kickoff(row, kickoff)

        priced = list(by_fixture.get(row.fixture_id, {}).values())
        if not priced:
            # Normal days ahead of kickoff: the feed publishes a matchday block
            # at a time of its choosing, not ours.
            report.unpriced += 1
        elif _attach_odds(row, priced):
            report.priced += 1

        incoming_selections.extend(
            _selection_row(
                selection,
                captured_at=card.card_generated_at,
                config_status=bets_today.config_status or rule.status,
                rule_status=rule.status,
                rule_reference=rule.reference,
            )
            for selection in priced
        )

    report.rule_bets = sum(1 for row in incoming_selections if row.stake_fraction > 0)

    for row in rows:
        apply_pricing(row)

    # ── 2b. Clubs and crests ─────────────────────────────────────────────────
    # Before the upsert, so a fixture is stored with its crest already attached
    # rather than gaining one a sync later.
    report.clubs, report.missing_crests = link_fixtures(session, resolved)
    if report.missing_crests:
        report.warnings.append(
            f"{report.missing_crests} fixture side(s) have no crest; "
            "run scripts/backfill_crests.py to fill the registry."
        )

    report.stored = _upsert(session, rows)

    # ── 2bb. StatPitch's own selections and daily pick ───────────────────────
    # After the fixture upsert, because a selection is a child of a fixture row
    # and the foreign key will not accept one that is not stored yet.
    ledgered = set(
        session.exec(
            select(StatPitchFixture.fixture_id).where(
                col(StatPitchFixture.fixture_id).in_([row.fixture_id for row in rows]),
                StatPitchFixture.ledgered.is_(True),
            )
        ).all()
    )
    report.selections = _upsert_selections(session, incoming_selections, ledgered)
    _upsert_bet_day(session, window.today, bets_today)

    # ── 2c. Match of the day ─────────────────────────────────────────────────
    # After the upsert, so the pick refers to a fixture that is actually stored.
    # Idempotent: the second sync of the day must not move it because a price
    # moved.
    todays = [row for row in rows if row.match_date == window.today]
    pick = ensure_match_of_the_day(session, window.today, todays)
    report.match_of_the_day = None if pick is None else f"{pick.home_team} vs {pick.away_team}"

    # ── 3. Results ───────────────────────────────────────────────────────────
    unsettled = session.exec(
        select(StatPitchFixture).where(StatPitchFixture.actual_result.is_(None))
    ).all()

    if unsettled:
        # `fetch_scores` never raises: ESPN needs no credential and bills
        # nothing, so the whole class of "not configured" and "quota exhausted"
        # failure is gone. A competition that fails becomes a warning and the
        # rest still settle.
        scores = await fetch_scores(competitions, days_back=3)
        report.warnings.extend(scores.warnings)
        report.settled = apply_scores(session, unsettled, scores.scores)

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
        "Sync complete: fetched=%d stored=%d priced=%d selections=%d rule_bets=%d "
        "settled=%d ledgered=%d pruned=%d",
        report.fetched,
        report.stored,
        report.priced,
        report.selections,
        report.rule_bets,
        report.settled,
        report.ledgered,
        report.pruned,
    )
    return report

"""Today's pick, read back from cache.

Served from `statpitch_bet_day` and `statpitch_selection` rather than proxied
upstream. StatPitch is a free instance that sleeps after fifteen minutes idle,
so a proxied read would pay a cold start of tens of seconds on the endpoint
most likely to be hit first — and would drop the caveats entirely whenever it
was unreachable, which is the worst possible moment to lose them.

The caveat is the reason this module is careful rather than a two-line query. A
recommendation carries a claim, and the statement qualifying that claim is not
decoration: the rule behind these picks has five seasons of measured closing-line
value but runs on a 25-book panel that has none, so its calibration is inherited
rather than re-measured. A reader shown the pick without that has been told
something untrue by us, not by StatPitch. So a pick is never served bare — if
the stored caveat is missing, one is built from the rule status carried on the
row itself.
"""

from datetime import date

from sqlmodel import Session, col, select

from api.statpitch.accounts.models import Tier
from api.statpitch.models import (
    BetPickRead,
    BetsTodayRead,
    StatPitchBetDay,
    StatPitchFixture,
    StatPitchSelection,
)
from api.statpitch.tiers import visible_competitions

# The status a rule reaches once its calibration has been measured on the panel
# it actually runs on. Anything short of it needs saying out loud.
FITTED = "fitted"


def caveat_for(status: str | None) -> str | None:
    """The statement that has to accompany a pick from an unfitted rule.

    Only a fallback. The upstream string is better written and more specific,
    and is preferred whenever it was stored; this exists so that a sync which
    fetched the card but not `/bets/today` cannot result in a recommendation
    rendered with nothing qualifying it.
    """
    if status == FITTED:
        return None
    if status is None:
        return (
            "This pick's selection rule publishes no calibration status, so how "
            "well measured it is cannot be established. Treat it as unvalidated."
        )
    return (
        f"selection_rule.status={status}. This pick came from a rule that is not yet "
        "fitted, so its calibration is inherited rather than measured on the price "
        "panel it now runs on. Treat it as a live test of a rule, not a validated edge."
    )


def _staked_selections(
    session: Session, day: date, tier: Tier
) -> list[tuple[StatPitchSelection, StatPitchFixture]]:
    """Every staked selection on `day`, with its fixture, scoped to the tier.

    Scoped in SQL rather than filtered afterwards, so a free caller's counts
    describe what they actually received.
    """
    rows = session.exec(
        select(StatPitchSelection, StatPitchFixture)
        .join(
            StatPitchFixture,
            col(StatPitchSelection.fixture_id) == col(StatPitchFixture.fixture_id),
        )
        .where(
            StatPitchFixture.match_date == day,
            StatPitchSelection.stake_fraction > 0,
            col(StatPitchFixture.competition_id).in_(visible_competitions(tier)),
        )
        .order_by(col(StatPitchSelection.stake_fraction).desc())
    ).all()
    return list(rows)


def _pick(selection: StatPitchSelection, fixture: StatPitchFixture) -> BetPickRead:
    """One staked selection, carrying enough fixture to render on its own."""
    return BetPickRead(
        **{
            field: getattr(selection, field)
            for field in BetPickRead.model_fields
            if hasattr(selection, field)
        }
        | {
            "fixture_id": fixture.fixture_id,
            "competition_id": fixture.competition_id,
            "competition_name": fixture.competition_name,
            "competition_short_name": fixture.competition_short_name,
            "competition_icon_url": fixture.competition_icon_url,
            "home_team": fixture.home_team,
            "away_team": fixture.away_team,
            "home_crest_url": fixture.home_crest_url,
            "away_crest_url": fixture.away_crest_url,
            "match_date": fixture.match_date,
            "commence_time": fixture.commence_time,
        }
    )


def build_bets_today(session: Session, day: date, tier: Tier) -> BetsTodayRead:
    """The day's picks and everything that qualifies them.

    An empty day is a normal answer, not a 404. Most days produce no qualifying
    bet at all — the rule fires only where a book misprices against the
    benchmark — so `reason` explains the absence rather than the response
    pretending the resource is missing.
    """
    stored = session.exec(select(StatPitchBetDay).where(StatPitchBetDay.match_date == day)).first()

    staked = _staked_selections(session, day, tier)
    bets = [_pick(selection, fixture) for selection, fixture in staked]

    # Prefer the status the rows themselves carry: they are what these picks
    # were actually recommended under, which is not necessarily what the day
    # summary says if a promotion landed between the two fetches.
    row_status = next((s.selection_rule_status for s, _ in staked), None)
    rule_status = row_status or (stored.selection_rule_status if stored else None)
    config_status = next((s.config_status for s, _ in staked), None) or (
        stored.config_status if stored else None
    )

    caveat = stored.caveat if stored else None
    if bets and not caveat:
        caveat = caveat_for(rule_status)

    if stored is None:
        return BetsTodayRead(
            match_date=day,
            bets=bets,
            count=len(bets),
            assessed=0,
            qualified_by_rule=sum(1 for selection, _ in staked if selection.rule_qualified),
            total_exposure=round(sum(selection.stake_fraction for selection, _ in staked), 6),
            caveat=caveat,
            confidence_caveat=None,
            disclaimer=None,
            reason=("No daily pick has been synced for this date yet. Run POST /statpitch/sync."),
            binding_constraint=None,
            empty_because=None,
            by_basis=None,
            selection_rule=None,
            config_status=config_status,
            selection_rule_status=rule_status,
            model_version=None,
            config_version=None,
            generated_at=None,
            synced_at=None,
        )

    return BetsTodayRead(
        match_date=day,
        bets=bets,
        # Counted from what is actually being returned rather than copied from
        # the upstream tally, which was taken across every competition and
        # before this tier's scope was applied.
        count=len(bets),
        assessed=stored.assessed,
        qualified_by_rule=stored.qualified_by_rule,
        total_exposure=stored.total_exposure,
        caveat=caveat,
        confidence_caveat=stored.confidence_caveat,
        disclaimer=stored.disclaimer,
        reason=stored.reason,
        binding_constraint=stored.binding_constraint,
        empty_because=stored.empty_because,
        by_basis=stored.by_basis,
        selection_rule=stored.selection_rule,
        config_status=config_status,
        selection_rule_status=rule_status,
        model_version=stored.model_version,
        config_version=stored.config_version,
        generated_at=stored.generated_at,
        synced_at=stored.synced_at,
    )

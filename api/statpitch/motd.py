"""The Match of the Day pick.

Until now this was recomputed on every request: whichever fixture happened to
have the highest win probability at the moment somebody asked. That is not a
pick, it is a query — it moves during the day as each sync refreshes the model's
numbers, so two people comparing notes an hour apart could be looking at
different matches, and a screenshot taken at lunchtime was wrong by dinner.

So it is chosen once, on the first sync of the day, and written down.

**One pick, for everybody.** It is on all three columns of the pricing page, so
a free account has to be able to see it — which means it can only be drawn from
the competitions the free tier can see. Picking globally would produce a
Champions League fixture that free accounts are not shown at all, and picking
per tier would mean there is no such thing as *the* match of the day.

**Confirmed kickoffs are preferred.** Roughly 88% of fixtures upstream sit on a
matchday placeholder rather than a real date, and billing one of those as
today's match is a claim the schedule does not support. A day with nothing
confirmed falls back to the rest rather than going without a pick.
"""

import logging
from datetime import UTC, date, datetime

from sqlmodel import Field, Session, SQLModel, col, select

from api.statpitch.models import StatPitchFixture
from api.statpitch.tiers import visible_competitions

log = logging.getLogger("statpitch.motd")


class StatPitchMatchOfTheDay(SQLModel, table=True):
    """One row per day, written once and then left alone.

    Kept out of `statpitch_fixture` deliberately. That table is a three-day
    cache; this is the record of a decision, and a decision that disappears when
    its subject is pruned is not much of a record.
    """

    __tablename__: str = "statpitch_match_of_the_day"

    id: int | None = Field(default=None, primary_key=True)
    # The Nicaragua-local day this is the pick for. Unique, which is what makes
    # `ensure` idempotent no matter how often the sync runs.
    match_date: date = Field(unique=True, index=True)
    fixture_id: str = Field(index=True, max_length=128)

    # Denormalised so the pick stays readable after the fixture is pruned. Not
    # authoritative — the fixture is — but a row saying only `fixture_id` is
    # useless three days later when somebody asks what was picked.
    competition_id: str = Field(max_length=64)
    home_team: str = Field(max_length=128)
    away_team: str = Field(max_length=128)
    # What it was picked on, kept so the choice can be second-guessed later.
    win_probability: float

    selected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _clearest_call(fixture: StatPitchFixture) -> float:
    """How confident the model is that this one has a winner.

    Deliberately not the best *bet*: that needs a price, and a pick everybody
    can see must not depend on odds coverage. The draw is excluded because "we
    are confident this is a draw" is not a match anyone wants recommended.
    """
    return max(fixture.home_win_prob, fixture.away_win_prob)


def choose(fixtures: list[StatPitchFixture]) -> StatPitchFixture | None:
    """The pick, from the fixtures a free account can see.

    Returns None only when there is genuinely nothing to pick from.
    """
    eligible = [
        fixture for fixture in fixtures if fixture.competition_id in visible_competitions("free")
    ]
    if not eligible:
        return None

    confirmed = [fixture for fixture in eligible if fixture.date_confirmed]
    return max(confirmed or eligible, key=_clearest_call)


def stored_for(db: Session, day: date) -> StatPitchMatchOfTheDay | None:
    return db.exec(
        select(StatPitchMatchOfTheDay).where(col(StatPitchMatchOfTheDay.match_date) == day)
    ).first()


def fixture_for(db: Session, day: date) -> StatPitchFixture | None:
    """Today's pick as a fixture, or None if there is not one to serve.

    Returns None when the pick has been pruned out of the cache as well as when
    none was made, because a caller wanting the fixture cannot do anything with
    a row that only remembers its name.
    """
    pick = stored_for(db, day)
    if pick is None:
        return None

    return db.exec(
        select(StatPitchFixture).where(StatPitchFixture.fixture_id == pick.fixture_id)
    ).first()


def ensure(db: Session, day: date, fixtures: list[StatPitchFixture]) -> StatPitchFixture | None:
    """Pick the day's match if it has not been picked yet.

    Idempotent, and that is the entire point: the second sync of the day must
    not move the pick just because a price moved. The one exception is a pick
    whose fixture has vanished from the cache — a postponement, say — where
    holding on to a match nobody can look at serves nobody.
    """
    existing = stored_for(db, day)
    if existing is not None:
        current = db.exec(
            select(StatPitchFixture).where(StatPitchFixture.fixture_id == existing.fixture_id)
        ).first()
        if current is not None:
            return current

        log.warning(
            "Match of the day %s for %s is no longer in the cache; picking again",
            existing.fixture_id,
            day,
        )
        db.delete(existing)
        db.commit()

    chosen = choose(fixtures)
    if chosen is None:
        return None

    db.add(
        StatPitchMatchOfTheDay(
            match_date=day,
            fixture_id=chosen.fixture_id,
            competition_id=chosen.competition_id,
            home_team=chosen.home_team,
            away_team=chosen.away_team,
            win_probability=_clearest_call(chosen),
        )
    )
    db.commit()

    log.info(
        "Match of the day for %s: %s vs %s (%.2f)",
        day,
        chosen.home_team,
        chosen.away_team,
        _clearest_call(chosen),
    )
    return chosen

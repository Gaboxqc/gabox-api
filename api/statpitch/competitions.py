"""The competition registry.

`competition_id` — "ENG.PL", "UEFA.UCL" — was a loose string in four tables,
with no table behind it. Nothing said what it meant, so the API returned
`"ENG.PL"` and no readable name at all, and there was nowhere to put an icon.

This is that table. It is referenced by the **natural key** rather than by a
surrogate id: `competition_id` is already stable, already unique, and already
what every query filters on, so pointing foreign keys at it normalises the data
without renaming a column or rewriting a single filter.

Twelve rows, seeded from `leagues.py` and never grown by user input — a
competition arriving from StatPitch that is not one of the twelve is a change we
would want to notice, not absorb silently.
"""

import logging
from datetime import UTC, datetime

from sqlmodel import Field, Session, SQLModel, col, select

from api.statpitch.leagues import ALL_COMPETITIONS, COMPETITION_NAMES, ESPN_LEAGUE_SLUGS

log = logging.getLogger("statpitch.competitions")


class StatPitchCompetition(SQLModel, table=True):
    """One competition. Permanent, like the club registry."""

    __tablename__: str = "statpitch_competition"

    id: int | None = Field(default=None, primary_key=True)
    # The natural key, and what everything else points at.
    competition_id: str = Field(unique=True, index=True, max_length=64)

    name: str = Field(max_length=128)
    # What fits in a filter chip: "Premier League", not "English Premier League".
    short_name: str = Field(max_length=64)
    # Which ESPN league this maps to, for the icon backfill.
    espn_slug: str = Field(max_length=64)

    # Null until the icon backfill has run, exactly like a club crest.
    icon_url: str | None = Field(default=None, max_length=512)
    icon_key: str | None = Field(default=None, max_length=256)
    icon_source: str | None = Field(default=None, max_length=32)
    icon_updated_at: datetime | None = Field(default=None)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def seed(db: Session) -> int:
    """Ensure all twelve competitions exist. Returns how many were added.

    Idempotent, and safe to call on every deploy: it fills gaps and leaves
    existing rows — including their icons — alone.
    """
    known = {row.competition_id for row in db.exec(select(StatPitchCompetition)).all()}
    added = 0

    for competition_id in sorted(ALL_COMPETITIONS):
        if competition_id in known:
            continue

        name, short_name = COMPETITION_NAMES[competition_id]
        db.add(
            StatPitchCompetition(
                competition_id=competition_id,
                name=name,
                short_name=short_name,
                espn_slug=ESPN_LEAGUE_SLUGS[competition_id],
            )
        )
        added += 1

    if added:
        db.commit()
        log.info("Seeded %d competition(s)", added)
    return added


def all_competitions(db: Session) -> list[StatPitchCompetition]:
    """Every competition, in a stable order for a filter list."""
    return db.exec(
        select(StatPitchCompetition).order_by(col(StatPitchCompetition.competition_id))
    ).all()


def by_id(db: Session, competition_id: str) -> StatPitchCompetition | None:
    return db.exec(
        select(StatPitchCompetition).where(StatPitchCompetition.competition_id == competition_id)
    ).first()

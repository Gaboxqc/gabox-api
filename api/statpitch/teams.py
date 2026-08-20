"""The club registry — a permanent home for anything true about a team.

`statpitch_fixture` is a cache with a three-day life. A crest URL cannot live
only there: it would be discarded every three days and have to be resolved again
on every sync, which turns a one-off lookup into a permanent dependency on an
upstream that may not always answer.

So clubs get their own table, keyed on the normalised form of their name
(`matching.normalize`): case, accents, punctuation, founding years and
corporate-form tokens are folded away, and the alias table is applied, so
"Club Atletico de Madrid" and "Atletico Madrid" are one row.

It does *not* fold every cross-source spelling — "Espanyol" and "RCD Espanyol de
Barcelona" remain two slugs, as do "Bayern Munich" and "FC Bayern Munchen". That
is fine here, because only StatPitch writes to this table and it names a club
the same way every time. Joining a *different* source's names onto these rows is
fuzzy work, and it belongs to `matching.similarity` in the crest resolver rather
than to the key.

Rows are created on sight during sync, with no crest. Filling the crest in is a
separate job — see `scripts/backfill_crests.py` — so a club showing up for the
first time on a Tuesday never blocks or slows the sync that found it.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, Session, SQLModel, col, select

from api.statpitch.matching import normalize
from api.statpitch.models import StatPitchFixture

log = logging.getLogger("statpitch.teams")


class StatPitchTeam(SQLModel, table=True):
    """One club. Permanent — never pruned, unlike the fixtures that reference it."""

    __tablename__: str = "statpitch_team"

    id: int | None = Field(default=None, primary_key=True)

    # `matching.normalize()` output: accent-stripped, punctuation-free,
    # corporate-noise removed, aliases applied. Stable for any one source; not a
    # cross-source identity. See the module docstring.
    slug: str = Field(unique=True, index=True, max_length=128)
    # Exactly what StatPitch sent the first time we saw the club, kept so a bad
    # match is diagnosable without replaying a sync.
    source_name: str = Field(max_length=128)
    # The nicest name available — the source name until a crest resolver
    # supplies something better.
    display_name: str = Field(max_length=128)
    # First competition the club appeared in. Informational: clubs move between
    # competitions and play in cups, so this is not an identity.
    competition_id: str = Field(index=True, max_length=64)

    # Extra spellings that should resolve to this row. Populated by hand for the
    # cases normalisation cannot reach; consulted before a new row is created.
    aliases: list[str] | None = Field(default=None, sa_column=Column(JSON, nullable=True))

    # ── Crest ─────────────────────────────────────────────────────────────────
    # Null until the backfill has run. A null crest is a normal state, not an
    # error: the UI falls back to a monogram.
    crest_url: str | None = Field(default=None, max_length=512)
    # The object key behind `crest_url`, so a stale file can be found and
    # replaced without parsing the URL.
    crest_key: str | None = Field(default=None, max_length=256)
    # Which upstream the crest came from, for when one of them turns out to be
    # wrong across the board.
    crest_source: str | None = Field(default=None, max_length=32)
    crest_updated_at: datetime | None = Field(default=None)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def slug_for(name: str) -> str:
    """The registry key for a club name.

    Falls back to a bare lowercase when normalisation strips everything, which
    it can for a name made entirely of corporate-form tokens. An empty slug
    would collapse every such club into one row.
    """
    return normalize(name) or name.strip().lower()


def resolve_team(db: Session, name: str, competition_id: str) -> StatPitchTeam:
    """Find the club, or record it. Never returns None.

    An unknown club is created rather than skipped, because the registry's job
    is to know every club that has ever appeared — including the ones with no
    crest yet. Those are precisely what the backfill needs a list of.
    """
    slug = slug_for(name)

    team = db.exec(select(StatPitchTeam).where(StatPitchTeam.slug == slug)).first()
    if team is not None:
        return team

    team = StatPitchTeam(
        slug=slug,
        source_name=name,
        display_name=name,
        competition_id=competition_id,
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    log.info("Registered new club %r (%s) in %s", name, slug, competition_id)
    return team


def teams_by_slug(db: Session, slugs: set[str]) -> dict[str, StatPitchTeam]:
    if not slugs:
        return {}
    rows = db.exec(select(StatPitchTeam).where(col(StatPitchTeam.slug).in_(slugs))).all()
    return {row.slug: row for row in rows}


def link_fixtures(db: Session, fixtures: list[StatPitchFixture]) -> tuple[int, int]:
    """Register every club in `fixtures` and copy its crest onto the row.

    Returns `(clubs_seen, sides_without_a_crest)`.

    The crest URL is denormalised onto the fixture rather than joined at read
    time. The fixture table is already a cache, the read path is a single-table
    query that should stay that way, and the columns have been sitting there
    unused since the schema was written.
    """
    if not fixtures:
        return 0, 0

    wanted = {
        slug_for(name) for fixture in fixtures for name in (fixture.home_team, fixture.away_team)
    }
    known = teams_by_slug(db, wanted)

    missing_crest = 0
    for fixture in fixtures:
        for side in ("home", "away"):
            name = getattr(fixture, f"{side}_team")
            slug = slug_for(name)

            team = known.get(slug)
            if team is None:
                team = resolve_team(db, name, fixture.competition_id)
                known[slug] = team

            setattr(fixture, f"{side}_crest_url", team.crest_url)
            if team.crest_url is None:
                missing_crest += 1

    return len(known), missing_crest

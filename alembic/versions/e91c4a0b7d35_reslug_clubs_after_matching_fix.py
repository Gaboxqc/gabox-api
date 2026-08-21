"""Re-slug the club registry after the name-matching fix

Revision ID: e91c4a0b7d35
Revises: d70b9c4e15af
Create Date: 2026-08-21 09:00:00.000000

`statpitch_team.slug` is `matching.normalize()` output, so widening the
noise-token list changes what the same club normalises to. Six rows went stale
the moment "stade" and "olympique" became noise:

    Stade Brestois 29         stade brestois        -> brestois
    Olympique de Marseille    olympique marseille   -> marseille
    Olympique Lyonnais        olympique lyonnais    -> lyon
    Deportivo Alaves          deportivo alaves      -> alaves
    RCD Espanyol de Barcelona espanyol barcelona    -> espanyol
    FC Internazionale Milano  internazionale milano -> inter milan

Left alone, the next sync would not recognise those clubs and would register a
second row for each — six duplicate clubs, and the crest attached to whichever
copy the backfill happened to fill.

So every row is re-slugged from its `source_name` using the current
normalisation. Two rows can now collapse onto one slug (a club recorded under
both its long and short name); where that happens the row holding a crest wins,
and failing that the older one, because the loser is a duplicate either way.

This revision imports application code on purpose. The rule being applied *is*
`matching.normalize`, and restating it here would be a second copy free to drift
from the one that matters.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e91c4a0b7d35"
down_revision: str | Sequence[str] | None = "d70b9c4e15af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    from api.statpitch.teams import slug_for

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, source_name, slug, crest_url FROM statpitch_team ORDER BY id"
        )
    ).fetchall()

    # Resolve collisions before writing anything, so the unique index is never
    # asked to hold two rows at once.
    winners: dict[str, tuple] = {}
    losers: list[int] = []

    for row in rows:
        wanted = slug_for(row.source_name)
        held = winners.get(wanted)

        if held is None:
            winners[wanted] = row
            continue

        # A crest beats no crest; otherwise the older row keeps its place.
        if row.crest_url and not held.crest_url:
            losers.append(held.id)
            winners[wanted] = row
        else:
            losers.append(row.id)

    for identifier in losers:
        connection.execute(
            sa.text("DELETE FROM statpitch_team WHERE id = :id"), {"id": identifier}
        )

    for wanted, row in winners.items():
        if row.slug != wanted:
            connection.execute(
                sa.text("UPDATE statpitch_team SET slug = :slug WHERE id = :id"),
                {"slug": wanted, "id": row.id},
            )


def downgrade() -> None:
    """Downgrade schema.

    A no-op, deliberately. The old slugs were wrong — "Stade Brestois 29" filed
    under `stade brestois` is what let the crest resolver hand it Rennes's badge
    — and a downgrade that restored them would reintroduce the defect. Rolling
    back the code is enough; the next sync re-registers anything missing.
    """

"""Add the Primeira Liga, Eredivisie and Super Lig to the registry

Revision ID: d8b1c37e4a05
Revises: c1e94a7b3d60
Create Date: 2026-09-04 12:00:00.000000

StatPitch went from twelve competitions to fifteen. `statpitch_fixture` and
`statpitch_settled_bet` both carry a foreign key onto
`statpitch_competition.competition_id`, so a sync that meets a
`POR.PRIMEIRA` fixture before its registry row exists fails on insert — which
is why this has to land before the first sync of the new scope, not alongside
it.

**Idempotent on purpose, and not merely as a courtesy.** The original registry
migration (`a1f5c62b70e4`) seeds by iterating `leagues.ALL_COMPETITIONS`, which
is live application code rather than a literal frozen into the revision. Adding
three entries to that constant therefore changed what that older migration does
on a database built from scratch: it now inserts fifteen rows, not twelve. So
there are two arrivals to survive —

    existing database   12 rows already present, this inserts 3
    fresh database      15 already seeded upstream, this inserts 0

— and a plain INSERT would raise a duplicate key on the second. The guard is
what makes `alembic upgrade head` work on both, and `tests/test_migrations.py`
builds the fresh case on every run.

The three IDs are Club Elo ISO-3 country codes: `POR`, `NED`, `TUR`, not the
ISO-2 codes a reader might expect. They are StatPitch's identifiers and are
matched exactly.

Additive only. The downgrade removes exactly the three rows this added, and
will refuse rather than cascade if a fixture already references one.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8b1c37e4a05"
down_revision: str | Sequence[str] | None = "c1e94a7b3d60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen here rather than imported from `leagues.py`. That import is precisely
# what made the earlier revision's behaviour change under it, and a migration
# has to keep doing the same thing years later regardless of what the constants
# have become since.
_NEW_COMPETITIONS = (
    ("POR.PRIMEIRA", "Portuguese Primeira Liga", "Primeira Liga", "por.1"),
    ("NED.EREDIVISIE", "Dutch Eredivisie", "Eredivisie", "ned.1"),
    ("TUR.SUPERLIG", "Turkish Super Lig", "Super Lig", "tur.1"),
)


def upgrade() -> None:
    """Upgrade schema."""
    from datetime import UTC, datetime

    connection = op.get_bind()
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")

    insert = sa.text(
        "INSERT INTO statpitch_competition "
        "(competition_id, name, short_name, espn_slug, created_at) "
        "SELECT :competition_id, :name, :short_name, :slug, :created "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM statpitch_competition WHERE competition_id = :competition_id"
        ")"
    )

    for competition_id, name, short_name, slug in _NEW_COMPETITIONS:
        connection.execute(
            insert,
            {
                "competition_id": competition_id,
                "name": name,
                "short_name": short_name,
                "slug": slug,
                "created": now,
            },
        )


def downgrade() -> None:
    """Downgrade schema.

    Deletes only rows nothing references. A competition with fixtures or settled
    bets against it is left in place: the foreign keys would refuse the delete
    anyway, and dropping the history to make the row removable is the wrong
    trade — the same reasoning the ledger's RESTRICT keys are built on.
    """
    connection = op.get_bind()

    delete = sa.text(
        "DELETE FROM statpitch_competition "
        "WHERE competition_id = :competition_id "
        "  AND NOT EXISTS ("
        "    SELECT 1 FROM statpitch_fixture WHERE competition_id = :competition_id"
        "  ) "
        "  AND NOT EXISTS ("
        "    SELECT 1 FROM statpitch_settled_bet WHERE competition_id = :competition_id"
        "  )"
    )

    for competition_id, *_ in _NEW_COMPETITIONS:
        connection.execute(delete, {"competition_id": competition_id})

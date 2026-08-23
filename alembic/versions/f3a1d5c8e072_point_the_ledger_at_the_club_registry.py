"""Point the ledger and the daily pick at the club registry

Revision ID: f3a1d5c8e072
Revises: a1f5c62b70e4
Create Date: 2026-08-22 09:00:00.000000

The last two places a club's name was copied rather than referenced:
`statpitch_settled_bet` and `statpitch_match_of_the_day`.

Both are records rather than caches, which is why they were left alone when the
fixtures were normalised — the argument being that each has to stay readable
after the fixture it came from is pruned. That argument was weaker than it
looked. `statpitch_team` is permanent and never pruned, so a reference reads for
exactly as long as a copy would, and says the club's current name rather than
whichever spelling was current the morning the row was written.

The keys are `ondelete RESTRICT`, deliberately. These tables are the reason a
club must not be deletable: refusing the delete is right, and cascading a
season's results away to make a club removable is not a trade anybody wants
offered. It is also a real guard — an earlier cleanup deleted six duplicate club
rows by hand, which this would have refused had any of them been on the books.

Backfill resolves each stored name through `matching.normalize`, exactly as the
sync does, and registers anything the registry has somehow never seen: the
columns are about to become NOT NULL, so nothing may be left unmatched.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a1d5c8e072"
down_revision: str | Sequence[str] | None = "a1f5c62b70e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("statpitch_settled_bet", "statpitch_match_of_the_day")


def upgrade() -> None:
    """Upgrade schema."""
    from datetime import UTC, datetime

    from api.statpitch.teams import slug_for

    connection = op.get_bind()
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")

    teams = {
        row.slug: row.id
        for row in connection.execute(sa.text("SELECT id, slug FROM statpitch_team")).fetchall()
    }

    def team_id(name: str, competition_id: str) -> int:
        slug = slug_for(name)
        if slug not in teams:
            connection.execute(
                sa.text(
                    "INSERT INTO statpitch_team "
                    "(slug, source_name, display_name, competition_id, created_at) "
                    "VALUES (:slug, :name, :name, :competition, :created)"
                ),
                {"slug": slug, "name": name, "competition": competition_id, "created": now},
            )
            teams[slug] = connection.execute(
                sa.text("SELECT id FROM statpitch_team WHERE slug = :slug"), {"slug": slug}
            ).scalar_one()
        return teams[slug]

    for table in _TABLES:
        op.add_column(table, sa.Column("home_team_id", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("away_team_id", sa.Integer(), nullable=True))

        rows = connection.execute(
            sa.text(  # noqa: S608 - table names are fixed above
                f"SELECT id, competition_id, home_team, away_team FROM {table}"
            )
        ).fetchall()

        for row in rows:
            connection.execute(
                sa.text(  # noqa: S608 - table names are fixed above
                    f"UPDATE {table} SET home_team_id = :home, away_team_id = :away "
                    "WHERE id = :id"
                ),
                {
                    "home": team_id(row.home_team, row.competition_id),
                    "away": team_id(row.away_team, row.competition_id),
                    "id": row.id,
                },
            )

        with op.batch_alter_table(table) as batch:
            batch.alter_column("home_team_id", existing_type=sa.Integer(), nullable=False)
            batch.alter_column("away_team_id", existing_type=sa.Integer(), nullable=False)
            batch.create_foreign_key(
                f"fk_{table}_home_team",
                "statpitch_team",
                ["home_team_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch.create_foreign_key(
                f"fk_{table}_away_team",
                "statpitch_team",
                ["away_team_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch.drop_column("home_team")
            batch.drop_column("away_team")

        op.create_index(op.f(f"ix_{table}_home_team_id"), table, ["home_team_id"], unique=False)
        op.create_index(op.f(f"ix_{table}_away_team_id"), table, ["away_team_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    connection = op.get_bind()

    for table in _TABLES:
        op.drop_index(op.f(f"ix_{table}_away_team_id"), table_name=table)
        op.drop_index(op.f(f"ix_{table}_home_team_id"), table_name=table)

        op.add_column(table, sa.Column("home_team", sa.String(length=128), nullable=True))
        op.add_column(table, sa.Column("away_team", sa.String(length=128), nullable=True))

        # Copy the names back out, so the old shape is populated rather than
        # merely present.
        connection.execute(
            sa.text(  # noqa: S608 - table names are fixed above
                f"UPDATE {table} SET "
                "home_team = (SELECT display_name FROM statpitch_team WHERE id = home_team_id), "
                "away_team = (SELECT display_name FROM statpitch_team WHERE id = away_team_id)"
            )
        )

        with op.batch_alter_table(table) as batch:
            batch.alter_column("home_team", existing_type=sa.String(length=128), nullable=False)
            batch.alter_column("away_team", existing_type=sa.String(length=128), nullable=False)
            batch.drop_constraint(f"fk_{table}_away_team", type_="foreignkey")
            batch.drop_constraint(f"fk_{table}_home_team", type_="foreignkey")
            batch.drop_column("away_team_id")
            batch.drop_column("home_team_id")

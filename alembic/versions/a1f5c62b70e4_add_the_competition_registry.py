"""Add the competition registry and point the module at it

Revision ID: a1f5c62b70e4
Revises: c4e70a91d825
Create Date: 2026-08-21 15:00:00.000000

`competition_id` — "ENG.PL", "UEFA.UCL" — was a bare string in four tables with
nothing behind it. The API returned the code and no readable name at all, and
there was nowhere to hang an icon.

The key is the **natural** one. `competition_id` is stable, unique, and already
what every filter matches on, so foreign keys point at it directly: the data is
normalised without renaming a column, rewriting a query, or migrating a single
value.

Order matters here. The twelve rows are seeded first, because the constraints
that follow would reject every existing row otherwise. Any competition already
present in the data but missing from `leagues.py` is inserted rather than
allowed to fail the migration — a competition StatPitch has sent us before is a
fact, whatever the constants say.

`statpitch_team.competition_id` gets no constraint: it records the first
competition a club was *seen* in, which is a note about history rather than a
reference, and a club can appear in a cup we do not track.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1f5c62b70e4"
down_revision: str | Sequence[str] | None = "c4e70a91d825"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REFERENCING = (
    "statpitch_fixture",
    "statpitch_settled_bet",
    "statpitch_match_of_the_day",
)


def upgrade() -> None:
    """Upgrade schema."""
    from datetime import UTC, datetime

    from api.statpitch.leagues import ALL_COMPETITIONS, COMPETITION_NAMES, ESPN_LEAGUE_SLUGS

    connection = op.get_bind()

    op.create_table(
        "statpitch_competition",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("competition_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("short_name", sa.String(length=64), nullable=False),
        sa.Column("espn_slug", sa.String(length=64), nullable=False),
        sa.Column("icon_url", sa.String(length=512), nullable=True),
        sa.Column("icon_key", sa.String(length=256), nullable=True),
        sa.Column("icon_source", sa.String(length=32), nullable=True),
        sa.Column("icon_updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_statpitch_competition_competition_id"),
        "statpitch_competition",
        ["competition_id"],
        unique=True,
    )

    # Bound as text rather than as a datetime: passing a datetime through raw
    # SQL trips SQLite's deprecated default adapter on 3.12, and both backends
    # accept an ISO timestamp for a DateTime column.
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    insert = sa.text(
        "INSERT INTO statpitch_competition "
        "(competition_id, name, short_name, espn_slug, created_at) "
        "VALUES (:competition_id, :name, :short_name, :slug, :created)"
    )

    for competition_id in sorted(ALL_COMPETITIONS):
        name, short_name = COMPETITION_NAMES[competition_id]
        connection.execute(
            insert,
            {
                "competition_id": competition_id,
                "name": name,
                "short_name": short_name,
                "slug": ESPN_LEAGUE_SLUGS[competition_id],
                "created": now,
            },
        )

    # Anything the data already refers to that the constants do not know about.
    # Named after itself rather than guessed at, so it is obvious on sight.
    for table in _REFERENCING:
        unknown = connection.execute(
            sa.text(
                f"SELECT DISTINCT competition_id FROM {table} "  # noqa: S608 - fixed names
                "WHERE competition_id NOT IN (SELECT competition_id FROM statpitch_competition)"
            )
        ).scalars()
        for competition_id in unknown:
            connection.execute(
                insert,
                {
                    "competition_id": competition_id,
                    "name": competition_id,
                    "short_name": competition_id,
                    "slug": "",
                    "created": now,
                },
            )

    for table in _REFERENCING:
        with op.batch_alter_table(table) as batch:
            batch.create_foreign_key(
                f"fk_{table}_competition",
                "statpitch_competition",
                ["competition_id"],
                ["competition_id"],
            )


def downgrade() -> None:
    """Downgrade schema."""
    for table in _REFERENCING:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_competition", type_="foreignkey")

    op.drop_index(
        op.f("ix_statpitch_competition_competition_id"), table_name="statpitch_competition"
    )
    op.drop_table("statpitch_competition")

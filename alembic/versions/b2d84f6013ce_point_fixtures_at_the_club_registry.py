"""Point fixtures at the club registry instead of copying names and crests

Revision ID: b2d84f6013ce
Revises: e91c4a0b7d35
Create Date: 2026-08-21 11:00:00.000000

`statpitch_fixture` held four columns that were copies of `statpitch_team`:
`home_team`, `away_team`, `home_crest_url`, `away_crest_url`. Two foreign keys
replace all four.

The copies were not free. A crest resolved on Tuesday did not reach a fixture
cached on Monday until the next sync overwrote it, because the URL had already
been snapshotted; and a club's name existed in as many spellings as there were
rows quoting it. One reference means one answer.

Backfill resolves each name through `matching.normalize`, exactly as the sync
does, and registers any club the registry has somehow never seen — a fixture
with no club is not representable afterwards, so nothing may be left unmatched.

`home_team` and `away_team` still exist on `statpitch_settled_bet` and
`statpitch_match_of_the_day`, deliberately. Those are records rather than
caches: the ledger is immutable and the pick outlives the fixture it names, so
both need to stay readable after the row they came from is gone.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2d84f6013ce"
down_revision: str | Sequence[str] | None = "e91c4a0b7d35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    from datetime import UTC, datetime

    from api.statpitch.teams import slug_for

    connection = op.get_bind()

    op.add_column("statpitch_fixture", sa.Column("home_team_id", sa.Integer(), nullable=True))
    op.add_column("statpitch_fixture", sa.Column("away_team_id", sa.Integer(), nullable=True))

    teams = {
        row.slug: row.id
        for row in connection.execute(sa.text("SELECT id, slug FROM statpitch_team")).fetchall()
    }

    fixtures = connection.execute(
        sa.text("SELECT id, competition_id, home_team, away_team FROM statpitch_fixture")
    ).fetchall()

    for fixture in fixtures:
        ids = {}
        for side in ("home", "away"):
            name = getattr(fixture, f"{side}_team")
            slug = slug_for(name)

            if slug not in teams:
                # A club the registry never met. It cannot be skipped: the
                # column is about to become NOT NULL, and a fixture without a
                # club has nowhere to go.
                connection.execute(
                    sa.text(
                        "INSERT INTO statpitch_team "
                        "(slug, source_name, display_name, competition_id, created_at) "
                        "VALUES (:slug, :name, :name, :competition, :created)"
                    ),
                    {
                        "slug": slug,
                        "name": name,
                        "competition": fixture.competition_id,
                        "created": datetime.now(UTC).replace(tzinfo=None),
                    },
                )
                teams[slug] = connection.execute(
                    sa.text("SELECT id FROM statpitch_team WHERE slug = :slug"), {"slug": slug}
                ).scalar_one()

            ids[side] = teams[slug]

        connection.execute(
            sa.text(
                "UPDATE statpitch_fixture SET home_team_id = :home, away_team_id = :away "
                "WHERE id = :id"
            ),
            {"home": ids["home"], "away": ids["away"], "id": fixture.id},
        )

    # Only now can the columns carry a constraint, and only now are the copies
    # safe to drop.
    with op.batch_alter_table("statpitch_fixture") as batch:
        batch.alter_column("home_team_id", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("away_team_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            "fk_statpitch_fixture_home_team", "statpitch_team", ["home_team_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_statpitch_fixture_away_team", "statpitch_team", ["away_team_id"], ["id"]
        )
        batch.drop_column("home_crest_url")
        batch.drop_column("away_crest_url")
        batch.drop_column("home_team")
        batch.drop_column("away_team")

    op.create_index(
        op.f("ix_statpitch_fixture_home_team_id"),
        "statpitch_fixture",
        ["home_team_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_statpitch_fixture_away_team_id"),
        "statpitch_fixture",
        ["away_team_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    connection = op.get_bind()

    op.drop_index(op.f("ix_statpitch_fixture_away_team_id"), table_name="statpitch_fixture")
    op.drop_index(op.f("ix_statpitch_fixture_home_team_id"), table_name="statpitch_fixture")

    op.add_column("statpitch_fixture", sa.Column("home_team", sa.String(), nullable=True))
    op.add_column("statpitch_fixture", sa.Column("away_team", sa.String(), nullable=True))
    op.add_column("statpitch_fixture", sa.Column("home_crest_url", sa.String(), nullable=True))
    op.add_column("statpitch_fixture", sa.Column("away_crest_url", sa.String(), nullable=True))

    # Copy the names and crests back out of the registry, so the old shape is
    # populated rather than merely present.
    connection.execute(
        sa.text(
            "UPDATE statpitch_fixture SET "
            "home_team = (SELECT display_name FROM statpitch_team WHERE id = home_team_id), "
            "away_team = (SELECT display_name FROM statpitch_team WHERE id = away_team_id), "
            "home_crest_url = (SELECT crest_url FROM statpitch_team WHERE id = home_team_id), "
            "away_crest_url = (SELECT crest_url FROM statpitch_team WHERE id = away_team_id)"
        )
    )

    with op.batch_alter_table("statpitch_fixture") as batch:
        batch.alter_column("home_team", existing_type=sa.String(), nullable=False)
        batch.alter_column("away_team", existing_type=sa.String(), nullable=False)
        batch.drop_constraint("fk_statpitch_fixture_away_team", type_="foreignkey")
        batch.drop_constraint("fk_statpitch_fixture_home_team", type_="foreignkey")
        batch.drop_column("away_team_id")
        batch.drop_column("home_team_id")

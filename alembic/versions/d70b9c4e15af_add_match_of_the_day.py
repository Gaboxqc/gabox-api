"""Add the Match of the Day pick

Revision ID: d70b9c4e15af
Revises: a83f61d4c209
Create Date: 2026-08-20 15:00:00.000000

The pick used to be recomputed on every request — whichever fixture had the
highest win probability at the moment somebody asked. That moves during the day
as each sync refreshes the model, so two readers an hour apart could be looking
at different matches and a screenshot was wrong by dinner.

One row per day instead, written by the day's first sync and then left alone.
The unique constraint on `match_date` is what makes that idempotent however
often the sync runs.

Kept out of `statpitch_fixture` deliberately: that table is a three-day cache,
and this is the record of a decision. `home_team`, `away_team` and
`win_probability` are denormalised so the pick stays readable after its fixture
has been pruned.

Additive.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d70b9c4e15af"
down_revision: Union[str, Sequence[str], None] = "a83f61d4c209"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "statpitch_match_of_the_day",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_date", sa.Date(), nullable=False),
        sa.Column("fixture_id", sa.String(length=128), nullable=False),
        sa.Column("competition_id", sa.String(length=64), nullable=False),
        sa.Column("home_team", sa.String(length=128), nullable=False),
        sa.Column("away_team", sa.String(length=128), nullable=False),
        sa.Column("win_probability", sa.Float(), nullable=False),
        sa.Column("selected_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # Unique: one pick per day, and the constraint is what enforces it rather
    # than the code remembering to check.
    op.create_index(
        op.f("ix_statpitch_match_of_the_day_match_date"),
        "statpitch_match_of_the_day",
        ["match_date"],
        unique=True,
    )
    op.create_index(
        op.f("ix_statpitch_match_of_the_day_fixture_id"),
        "statpitch_match_of_the_day",
        ["fixture_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_statpitch_match_of_the_day_fixture_id"),
        table_name="statpitch_match_of_the_day",
    )
    op.drop_index(
        op.f("ix_statpitch_match_of_the_day_match_date"),
        table_name="statpitch_match_of_the_day",
    )
    op.drop_table("statpitch_match_of_the_day")

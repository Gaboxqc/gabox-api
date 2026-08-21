"""Add the free tier's prediction-unlock ledger

Revision ID: c9d5027ab318
Revises: f6c30d81ba47
Create Date: 2026-08-20 13:00:00.000000

The free tier is sold as "3 predictions per day", which needs somewhere to count
them. It cannot be memory: the API runs serverless, so every invocation is a
fresh process and an in-process tally would reset continuously and enforce
nothing — the same reason the login lockout lives in the database.

One row per fixture an account has revealed. Two details are load-bearing:

`fixture_id` rather than the fixture's primary key, because `statpitch_fixture`
is a three-day cache: a row can be pruned and come back with a new id, and an
unlock somebody already spent must not evaporate with it.

The unique constraint on (account_id, fixture_id) is what makes unlocking
idempotent. Opening the same fixture twice costs one unlock, not two, and
opening it again tomorrow costs nothing — anything else charges a reader for
refreshing the page. `unlocked_on` records which day the allowance came out of,
in Nicaragua-local terms so it rolls over when the app's "today" does.

Additive.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d5027ab318"
down_revision: Union[str, Sequence[str], None] = "f6c30d81ba47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "statpitch_prediction_unlock",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("fixture_id", sa.String(length=128), nullable=False),
        sa.Column("unlocked_on", sa.Date(), nullable=False),
        sa.Column("unlocked_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["statpitch_account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "fixture_id", name="uq_statpitch_unlock_account_fixture"),
    )
    op.create_index(
        op.f("ix_statpitch_prediction_unlock_account_id"),
        "statpitch_prediction_unlock",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_statpitch_prediction_unlock_fixture_id"),
        "statpitch_prediction_unlock",
        ["fixture_id"],
        unique=False,
    )
    # The daily count filters on this, per account, on every fixture response.
    op.create_index(
        op.f("ix_statpitch_prediction_unlock_unlocked_on"),
        "statpitch_prediction_unlock",
        ["unlocked_on"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_statpitch_prediction_unlock_unlocked_on"),
        table_name="statpitch_prediction_unlock",
    )
    op.drop_index(
        op.f("ix_statpitch_prediction_unlock_fixture_id"),
        table_name="statpitch_prediction_unlock",
    )
    op.drop_index(
        op.f("ix_statpitch_prediction_unlock_account_id"),
        table_name="statpitch_prediction_unlock",
    )
    op.drop_table("statpitch_prediction_unlock")

"""Add trial requests

Revision ID: d5b83f1a97c4
Revises: f3a1d5c8e072
Create Date: 2026-08-22 16:00:00.000000

The 14-day Pro trial was self-serve: a customer pressed a button and had it. It
is now asked for and granted, so there is something to record — who asked, when,
who decided and what they decided.

One row per request, moving through one small state machine:

    pending ──approve──> approved
            └─decline──> declined

Nothing moves backwards, and a decided request is never re-decided: overwriting
`decided_by` and `decided_at` would erase the part worth keeping. A customer who
was turned down asks again, which opens a new row.

Additive. `statpitch_account.trial_used_at` keeps its meaning exactly — stamped
when a trial is actually granted, and cleared only by an admin reset — so
nothing about existing accounts changes.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5b83f1a97c4"
down_revision: str | Sequence[str] | None = "f3a1d5c8e072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "statpitch_trial_request",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("decision_reason", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["statpitch_account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_statpitch_trial_request_account_id"),
        "statpitch_trial_request",
        ["account_id"],
        unique=False,
    )
    # The queue filters on this and orders by the other.
    op.create_index(
        op.f("ix_statpitch_trial_request_status"),
        "statpitch_trial_request",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_statpitch_trial_request_requested_at"),
        "statpitch_trial_request",
        ["requested_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_statpitch_trial_request_requested_at"), table_name="statpitch_trial_request"
    )
    op.drop_index(op.f("ix_statpitch_trial_request_status"), table_name="statpitch_trial_request")
    op.drop_index(
        op.f("ix_statpitch_trial_request_account_id"), table_name="statpitch_trial_request"
    )
    op.drop_table("statpitch_trial_request")

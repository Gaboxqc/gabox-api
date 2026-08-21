"""Add the tier grant history

Revision ID: c4e70a91d825
Revises: b2d84f6013ce
Create Date: 2026-08-21 13:00:00.000000

`statpitch_account.tier` is current state: one row, always the truth about
today, and incapable of saying how it got that way. This is the event log behind
it — append-only, one row per change, never updated.

That is a different thing from the copies this schema has been removing. A
duplicated crest URL was a second answer to a question that already had one;
this answers a question the account row cannot answer at all: *why is this
account Elite, and until when?*

`reason` is NOT NULL on purpose. A grant nobody explained is the one that makes
no sense six months later, when somebody emails asking why their subscription
ended.

Additive.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e70a91d825"
down_revision: str | Sequence[str] | None = "b2d84f6013ce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "statpitch_tier_grant",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("from_tier", sa.String(length=16), nullable=False),
        sa.Column("to_tier", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("granted_by", sa.String(length=64), nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["statpitch_account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_statpitch_tier_grant_account_id"),
        "statpitch_tier_grant",
        ["account_id"],
        unique=False,
    )
    # History is read newest-first, per account.
    op.create_index(
        op.f("ix_statpitch_tier_grant_granted_at"),
        "statpitch_tier_grant",
        ["granted_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_statpitch_tier_grant_granted_at"), table_name="statpitch_tier_grant")
    op.drop_index(op.f("ix_statpitch_tier_grant_account_id"), table_name="statpitch_tier_grant")
    op.drop_table("statpitch_tier_grant")

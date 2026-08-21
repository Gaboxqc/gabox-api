"""Add account-scoped StatPitch API keys

Revision ID: a83f61d4c209
Revises: c9d5027ab318
Create Date: 2026-08-20 14:00:00.000000

Programmatic access is the one line Elite buys over Pro, so it needs keys.

Only the SHA-256 of a key is stored, for the same reason as session tokens: a
database leak must not hand over live credentials. `prefix` keeps the visible
stub — `sp_live_A1b2C3d4` — so a key can be identified in a list after the rest
of it is gone.

Keys are revoked, never deleted. One that turns up in a log a year later is
worth being able to identify, which dropping the row would prevent.

Additive.
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a83f61d4c209"
down_revision: Union[str, Sequence[str], None] = "c9d5027ab318"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "statpitch_api_key",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=32), nullable=False),
        sa.Column("key_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["statpitch_account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Every authenticated API request is an equality lookup on this, so it is
    # the one index that has to exist.
    op.create_index(
        op.f("ix_statpitch_api_key_key_hash"), "statpitch_api_key", ["key_hash"], unique=True
    )
    op.create_index(
        op.f("ix_statpitch_api_key_account_id"), "statpitch_api_key", ["account_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_statpitch_api_key_account_id"), table_name="statpitch_api_key")
    op.drop_index(op.f("ix_statpitch_api_key_key_hash"), table_name="statpitch_api_key")
    op.drop_table("statpitch_api_key")

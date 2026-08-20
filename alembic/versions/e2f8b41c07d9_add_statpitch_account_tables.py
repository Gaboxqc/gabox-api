"""Add StatPitch customer account tables

Revision ID: e2f8b41c07d9
Revises: a71c4d8e2f30
Create Date: 2026-08-20 11:00:00.000000

StatPitch is becoming a paid product with free, Pro and Elite tiers, so it needs
customer accounts of its own.

Purely additive. The admin tables (`admin_user`, `admin_session`,
`admin_login_attempt`) are untouched: a customer must never be able to become an
admin, and the strongest form of that guarantee is that the two share no row and
no table. It also means this revision can be rolled back without the portfolio
dashboard noticing.

`tier` and `tier_source` carry a server default so that an INSERT which forgets
them lands on the *least* privileged value rather than failing open.
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2f8b41c07d9"
down_revision: Union[str, Sequence[str], None] = "a71c4d8e2f30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "statpitch_account",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("email_verified_at", sa.DateTime(), nullable=True),
        sa.Column(
            "tier", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="free"
        ),
        sa.Column("tier_expires_at", sa.DateTime(), nullable=True),
        sa.Column("tier_source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("tier_updated_at", sa.DateTime(), nullable=True),
        sa.Column("tier_updated_by", sa.String(length=64), nullable=True),
        sa.Column("trial_used_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_statpitch_account_email"), "statpitch_account", ["email"], unique=True)
    op.create_index(op.f("ix_statpitch_account_tier"), "statpitch_account", ["tier"], unique=False)

    op.create_table(
        "statpitch_account_session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("csrf_token", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=256), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["statpitch_account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_statpitch_account_session_token_hash"),
        "statpitch_account_session",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_statpitch_account_session_account_id"),
        "statpitch_account_session",
        ["account_id"],
        unique=False,
    )

    op.create_table(
        "statpitch_login_attempt",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_statpitch_login_attempt_email"), "statpitch_login_attempt", ["email"], unique=False
    )
    op.create_index(
        op.f("ix_statpitch_login_attempt_ip_address"),
        "statpitch_login_attempt",
        ["ip_address"],
        unique=False,
    )
    op.create_index(
        op.f("ix_statpitch_login_attempt_attempted_at"),
        "statpitch_login_attempt",
        ["attempted_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_statpitch_login_attempt_attempted_at"), table_name="statpitch_login_attempt"
    )
    op.drop_index(
        op.f("ix_statpitch_login_attempt_ip_address"), table_name="statpitch_login_attempt"
    )
    op.drop_index(op.f("ix_statpitch_login_attempt_email"), table_name="statpitch_login_attempt")
    op.drop_table("statpitch_login_attempt")

    op.drop_index(
        op.f("ix_statpitch_account_session_account_id"), table_name="statpitch_account_session"
    )
    op.drop_index(
        op.f("ix_statpitch_account_session_token_hash"), table_name="statpitch_account_session"
    )
    op.drop_table("statpitch_account_session")

    op.drop_index(op.f("ix_statpitch_account_tier"), table_name="statpitch_account")
    op.drop_index(op.f("ix_statpitch_account_email"), table_name="statpitch_account")
    op.drop_table("statpitch_account")

"""Add admin auth tables

Revision ID: b7f3c1d92a04
Revises: f44c971ab177
Create Date: 2026-08-07 23:05:00.000000

Adds password-based admin login: the single admin account, its server-side
sessions, and the login-attempt log the lockout check reads. Purely additive —
no existing table is touched, and the X-API-KEY path keeps working.
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7f3c1d92a04"
down_revision: Union[str, Sequence[str], None] = "f44c971ab177"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "admin_user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_admin_user_username"), "admin_user", ["username"], unique=True)

    op.create_table(
        "admin_session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("csrf_token", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=256), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["admin_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_admin_session_token_hash"), "admin_session", ["token_hash"], unique=True
    )
    op.create_index(op.f("ix_admin_session_user_id"), "admin_session", ["user_id"], unique=False)

    op.create_table(
        "admin_login_attempt",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_admin_login_attempt_username"), "admin_login_attempt", ["username"], unique=False
    )
    op.create_index(
        op.f("ix_admin_login_attempt_ip_address"),
        "admin_login_attempt",
        ["ip_address"],
        unique=False,
    )
    op.create_index(
        op.f("ix_admin_login_attempt_attempted_at"),
        "admin_login_attempt",
        ["attempted_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_admin_login_attempt_attempted_at"), table_name="admin_login_attempt")
    op.drop_index(op.f("ix_admin_login_attempt_ip_address"), table_name="admin_login_attempt")
    op.drop_index(op.f("ix_admin_login_attempt_username"), table_name="admin_login_attempt")
    op.drop_table("admin_login_attempt")

    op.drop_index(op.f("ix_admin_session_user_id"), table_name="admin_session")
    op.drop_index(op.f("ix_admin_session_token_hash"), table_name="admin_session")
    op.drop_table("admin_session")

    op.drop_index(op.f("ix_admin_user_username"), table_name="admin_user")
    op.drop_table("admin_user")

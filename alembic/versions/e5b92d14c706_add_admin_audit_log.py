"""Add admin audit log

Revision ID: e5b92d14c706
Revises: d3f70c8a91b5
Create Date: 2026-08-08 02:05:00.000000

Records successful admin writes: method, path, outcome and which credential
authorised them. No bodies and no query strings, so a credential can never end
up in here.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b92d14c706"
down_revision: Union[str, Sequence[str], None] = "d3f70c8a91b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("principal_kind", sa.String(length=20), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_admin_audit_log_at"), "admin_audit_log", ["at"], unique=False)
    op.create_index(op.f("ix_admin_audit_log_path"), "admin_audit_log", ["path"], unique=False)
    op.create_index(
        op.f("ix_admin_audit_log_username"), "admin_audit_log", ["username"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_admin_audit_log_username"), table_name="admin_audit_log")
    op.drop_index(op.f("ix_admin_audit_log_path"), table_name="admin_audit_log")
    op.drop_index(op.f("ix_admin_audit_log_at"), table_name="admin_audit_log")
    op.drop_table("admin_audit_log")

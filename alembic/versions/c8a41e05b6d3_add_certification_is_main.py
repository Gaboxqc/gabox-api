"""Add certification is_main

Revision ID: c8a41e05b6d3
Revises: b7f3c1d92a04
Create Date: 2026-08-07 23:40:00.000000

Mirrors `portfolio_project.is_main`. The frontend has always rendered a "Main
certifications" section and sent `is_main=true`, but the column did not exist,
so FastAPI dropped the unknown query parameter and the section quietly showed
unfiltered results.

`server_default` is required because existing rows need a value to satisfy the
NOT NULL constraint. It is deliberately *kept* rather than dropped afterwards:
`ALTER COLUMN ... DROP DEFAULT` is not supported by SQLite, and because SQLite
runs DDL non-transactionally it would abort mid-migration and leave the column
added but the index missing. Keeping the default is portable, and harmless — the
model supplies `False` on every insert anyway.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8a41e05b6d3"
down_revision: Union[str, Sequence[str], None] = "b7f3c1d92a04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "portfolio_certification",
        sa.Column("is_main", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        op.f("ix_portfolio_certification_is_main"),
        "portfolio_certification",
        ["is_main"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_portfolio_certification_is_main"), table_name="portfolio_certification"
    )
    op.drop_column("portfolio_certification", "is_main")

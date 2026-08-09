"""Add academy image url

Revision ID: d3f70c8a91b5
Revises: c8a41e05b6d3
Create Date: 2026-08-08 01:20:00.000000

Gives each academy a logo, shown beside its courses and certifications on the
public site. Previously the cards hardcoded a single academy's icon, which was
wrong for any other provider.

Nullable, so existing rows need no backfill and a record without a logo simply
falls back in the UI.
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3f70c8a91b5"
down_revision: Union[str, Sequence[str], None] = "c8a41e05b6d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "portfolio_academy",
        sa.Column("image_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("portfolio_academy", "image_url")

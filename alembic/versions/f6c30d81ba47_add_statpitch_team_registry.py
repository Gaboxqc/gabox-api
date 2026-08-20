"""Add the StatPitch club registry

Revision ID: f6c30d81ba47
Revises: e2f8b41c07d9
Create Date: 2026-08-20 12:00:00.000000

`statpitch_fixture` is a three-day cache, so nothing durable can live there. A
club crest is durable: resolving it costs an upstream lookup, and re-resolving
it every three days would turn a one-off job into a permanent dependency on an
API that may not always answer.

This table is where clubs live instead. Permanent, never pruned, keyed on the
normalised form of the club name — case, accents, punctuation and corporate-form
tokens folded away, so "Club Atletico de Madrid" and "Atletico Madrid" land on
one row. Only StatPitch writes to it, and it names a club consistently; matching
another source's names onto these rows is fuzzy work done by the crest resolver.

Additive, and the fixture table is untouched: `home_crest_url` and
`away_crest_url` have existed there since the schema was written and were only
ever waiting for a source.
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6c30d81ba47"
down_revision: Union[str, Sequence[str], None] = "e2f8b41c07d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "statpitch_team",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("source_name", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("competition_id", sa.String(length=64), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=True),
        sa.Column("crest_url", sa.String(length=512), nullable=True),
        sa.Column("crest_key", sa.String(length=256), nullable=True),
        sa.Column("crest_source", sa.String(length=32), nullable=True),
        sa.Column("crest_updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_statpitch_team_slug"), "statpitch_team", ["slug"], unique=True)
    op.create_index(
        op.f("ix_statpitch_team_competition_id"),
        "statpitch_team",
        ["competition_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_statpitch_team_competition_id"), table_name="statpitch_team")
    op.drop_index(op.f("ix_statpitch_team_slug"), table_name="statpitch_team")
    op.drop_table("statpitch_team")

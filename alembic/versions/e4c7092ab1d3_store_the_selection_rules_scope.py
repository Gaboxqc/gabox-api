"""Store the selection rule's measured competition scope

Revision ID: e4c7092ab1d3
Revises: d8b1c37e4a05
Create Date: 2026-09-04 12:30:00.000000

`/bets/today` now publishes `selection_rule.competitions` — the competitions the
rule has been measured to earn in, and therefore the only ones that can produce
a bet. StatPitch prices eight leagues and the scope currently names six.

The whole rule blob is already stored in `statpitch_bet_day.selection_rule`, so
this column is a duplicate of one key inside it. That is deliberate: this is the
only part of the rule that gets **queried** rather than displayed. Deciding
whether a competition can produce a bet at all is a filter, and asking it of a
JSON blob means either a database-specific JSON operator or reading every row
into Python. A column is the honest shape for a question asked that way.

Per day, not per selection. The rule is a property of the card, and the day row
already records what a day was produced under — so when the scope moves on a
later re-measurement, every earlier day still reads as what it was.

Nullable, because every row written before this migration has no scope stored
and there is no correct value to backfill: the historical scope is not knowable
from here, and guessing today's answer onto past rows would assert something
that was never measured. Null means "not recorded", which is true.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4c7092ab1d3"
down_revision: str | Sequence[str] | None = "d8b1c37e4a05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "statpitch_bet_day",
        sa.Column("selection_rule_competitions", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("statpitch_bet_day", "selection_rule_competitions")

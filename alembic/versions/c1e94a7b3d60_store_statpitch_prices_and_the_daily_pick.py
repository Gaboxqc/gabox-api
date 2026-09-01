"""Store StatPitch's own prices, selections and daily pick

Revision ID: c1e94a7b3d60
Revises: d5b83f1a97c4
Create Date: 2026-09-01 10:00:00.000000

StatPitch prices its own card now, against a 25-book panel it did not have
before, and publishes it on three new endpoints. This lands the storage for it.

`statpitch_selection` is a table rather than more columns on the fixture. Each
selection carries four prices, three probabilities and five edges; across the
three 1X2 outcomes alone that is fifty columns onto the widest row in the
schema. It is keyed `(fixture_id, selection)` and refreshed in place rather
than by capture instant: this is cache, pruned with its fixture, and the
permanent record is `statpitch_settled_bet`. `captured_at` is kept as a column
so the age of a price stays legible without being part of the identity. The
foreign key cascades, unlike the ledger's RESTRICT — these rows should go when
the fixture is pruned rather than hold it back.

`statpitch_bet_day` holds one row per local day. It exists for the caveats:
day-level strings with no home on a fixture row, which are not decoration. A
reader shown a pick without the caveat qualifying it has been told something
untrue, and caching them means they still serve when the upstream free instance
is asleep — the exact moment they would otherwise go missing.

The four columns added to `statpitch_settled_bet` are per-row provenance, which
the integration contract asks for twice and for one reason: when the selection
rule is promoted from `experimental` to `fitted`, everything already banked
must still read as what it was recommended under. A status resolved at read
time could not do that.

Additive throughout. No column is dropped, no existing row is rewritten, and
every new column is nullable or defaulted, so the downgrade is a clean reversal.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1e94a7b3d60"
down_revision: str | Sequence[str] | None = "d5b83f1a97c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Added to the ledger, all nullable: null on our own `1x2` and `overall` rows,
# which are priced here and owe nothing to an upstream rule.
_LEDGER_PROVENANCE = (
    "selection_basis",
    "pricing",
    "config_status",
    "selection_rule_status",
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "statpitch_selection",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fixture_id", sa.String(), nullable=False),
        # StatPitch's own name, verbatim — "1x2_home", not "home_win".
        sa.Column("selection", sa.String(), nullable=False),
        # Ours, translated. Null means we have no name for it, which is how a
        # selection ends up displayed but never priced or settled.
        sa.Column("our_selection", sa.String(), nullable=True),
        sa.Column("market_family", sa.String(), nullable=False),
        sa.Column("line", sa.Float(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        # The four prices, never merged. `odds` is the only bettable one.
        sa.Column("reference_odds", sa.Float(), nullable=True),
        sa.Column("consensus_odds", sa.Float(), nullable=True),
        sa.Column("odds", sa.Float(), nullable=True),
        sa.Column("fair_odds", sa.Float(), nullable=True),
        sa.Column("p_model", sa.Float(), nullable=True),
        sa.Column("q_fair", sa.Float(), nullable=True),
        sa.Column("p_used", sa.Float(), nullable=True),
        sa.Column("edge_prob", sa.Float(), nullable=True),
        sa.Column("expected_value", sa.Float(), nullable=True),
        sa.Column("price_edge", sa.Float(), nullable=True),
        sa.Column("model_edge", sa.Float(), nullable=True),
        sa.Column("rule_edge", sa.Float(), nullable=True),
        sa.Column("rule_qualified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("grade", sa.String(), nullable=True),
        sa.Column("composite", sa.Float(), nullable=True),
        # 0.0 means assessed, not recommended.
        sa.Column("stake_fraction", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reasons", sa.JSON(), nullable=True),
        # Per-row provenance, never once per sync.
        sa.Column("config_status", sa.String(), nullable=True),
        sa.Column("selection_rule_status", sa.String(), nullable=True),
        sa.Column("selection_rule_reference", sa.String(), nullable=True),
        # Not published upstream as of 2026-09-01. Nullable, never inferred.
        sa.Column("selection_basis", sa.String(), nullable=True),
        sa.Column("pricing", sa.String(), nullable=True),
        sa.Column("model_odds", sa.Float(), nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["fixture_id"],
            ["statpitch_fixture.fixture_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fixture_id", "selection", name="uq_statpitch_selection"),
    )
    op.create_index(
        op.f("ix_statpitch_selection_fixture_id"), "statpitch_selection", ["fixture_id"]
    )
    op.create_index(op.f("ix_statpitch_selection_selection"), "statpitch_selection", ["selection"])
    op.create_index(
        op.f("ix_statpitch_selection_our_selection"), "statpitch_selection", ["our_selection"]
    )
    op.create_index(
        op.f("ix_statpitch_selection_market_family"), "statpitch_selection", ["market_family"]
    )
    op.create_index(
        op.f("ix_statpitch_selection_rule_qualified"), "statpitch_selection", ["rule_qualified"]
    )
    op.create_index(
        op.f("ix_statpitch_selection_stake_fraction"), "statpitch_selection", ["stake_fraction"]
    )
    op.create_index(
        op.f("ix_statpitch_selection_selection_basis"), "statpitch_selection", ["selection_basis"]
    )
    op.create_index(op.f("ix_statpitch_selection_pricing"), "statpitch_selection", ["pricing"])

    op.create_table(
        "statpitch_bet_day",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_date", sa.Date(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assessed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("qualified_by_rule", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_exposure", sa.Float(), nullable=False, server_default="0"),
        # The strings that have to reach the reader.
        sa.Column("caveat", sa.String(), nullable=True),
        sa.Column("confidence_caveat", sa.String(), nullable=True),
        sa.Column("disclaimer", sa.String(), nullable=True),
        # Why the day is empty, when it is. An empty day is a normal answer.
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("binding_constraint", sa.String(), nullable=True),
        sa.Column("empty_because", sa.JSON(), nullable=True),
        sa.Column("config_status", sa.String(), nullable=True),
        sa.Column("selection_rule_status", sa.String(), nullable=True),
        sa.Column("selection_rule", sa.JSON(), nullable=True),
        sa.Column("by_basis", sa.JSON(), nullable=True),
        # The advisory refusal — a caveat, not a failure.
        sa.Column("refusal_reason_code", sa.String(), nullable=True),
        sa.Column("refusal_reason", sa.String(), nullable=True),
        sa.Column("model_version", sa.String(), nullable=True),
        sa.Column("config_version", sa.String(), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_date", name="uq_statpitch_bet_day_date"),
    )
    op.create_index(op.f("ix_statpitch_bet_day_match_date"), "statpitch_bet_day", ["match_date"])

    for column in _LEDGER_PROVENANCE:
        op.add_column("statpitch_settled_bet", sa.Column(column, sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    for column in reversed(_LEDGER_PROVENANCE):
        op.drop_column("statpitch_settled_bet", column)

    op.drop_index(op.f("ix_statpitch_bet_day_match_date"), table_name="statpitch_bet_day")
    op.drop_table("statpitch_bet_day")

    op.drop_index(op.f("ix_statpitch_selection_pricing"), table_name="statpitch_selection")
    op.drop_index(op.f("ix_statpitch_selection_selection_basis"), table_name="statpitch_selection")
    op.drop_index(op.f("ix_statpitch_selection_stake_fraction"), table_name="statpitch_selection")
    op.drop_index(op.f("ix_statpitch_selection_rule_qualified"), table_name="statpitch_selection")
    op.drop_index(op.f("ix_statpitch_selection_market_family"), table_name="statpitch_selection")
    op.drop_index(op.f("ix_statpitch_selection_our_selection"), table_name="statpitch_selection")
    op.drop_index(op.f("ix_statpitch_selection_selection"), table_name="statpitch_selection")
    op.drop_index(op.f("ix_statpitch_selection_fixture_id"), table_name="statpitch_selection")
    op.drop_table("statpitch_selection")

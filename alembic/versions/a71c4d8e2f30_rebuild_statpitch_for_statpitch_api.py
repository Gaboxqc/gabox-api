"""Rebuild StatPitch against the real StatPitch API

Revision ID: a71c4d8e2f30
Revises: e5b92d14c706
Create Date: 2026-08-17 21:40:00.000000

`statpitch_match_prediction` was built for a different service: World Cup
national teams, head-to-head counts and a `btts` object. StatPitch is twelve
European club competitions, publishes no h2h at all, and returns `btts` as a
single float — so almost no column survived, and the old rows cannot be
reinterpreted under the new schema. They are dropped rather than migrated.

The replacement is two tables with deliberately different lifetimes.
`statpitch_fixture` is a cache pruned to yesterday/today/tomorrow.
`statpitch_settled_bet` is a permanent append-only ledger, which is the only
reason 7- and 30-day ROI can outlive a three-day retention policy.
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a71c4d8e2f30"
down_revision: Union[str, Sequence[str], None] = "e5b92d14c706"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _float_columns(*names: str) -> list[sa.Column]:
    return [sa.Column(name, sa.Float(), nullable=True) for name in names]


def upgrade() -> None:
    """Upgrade schema."""
    # Guarded because `statpitch_match_prediction` was never created by a
    # migration: revision 9c1ad9f14faf is an empty stamp, and the table was
    # built by SQLModel's create_all() out of band. It therefore exists on the
    # deployed database but not on one built by `alembic upgrade head`.
    if sa.inspect(op.get_bind()).has_table("statpitch_match_prediction"):
        op.drop_table("statpitch_match_prediction")

    op.create_table(
        "statpitch_fixture",
        sa.Column("id", sa.Integer(), nullable=False),
        # ── Identity ─────────────────────────────────────────────────────────
        sa.Column("fixture_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("competition_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("season", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("stage", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("format", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        # ── Scheduling ───────────────────────────────────────────────────────
        sa.Column("match_date", sa.Date(), nullable=False),
        sa.Column("source_date", sa.Date(), nullable=False),
        sa.Column("kickoff", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("commence_time", sa.DateTime(), nullable=True),
        sa.Column("date_confirmed", sa.Boolean(), nullable=False),
        sa.Column("home_team", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("away_team", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("neutral_venue", sa.Boolean(), nullable=False),
        sa.Column("odds_coverage", sa.Boolean(), nullable=False),
        sa.Column("home_crest_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("away_crest_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        # ── Provenance ───────────────────────────────────────────────────────
        sa.Column("prediction_source", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("model_version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("config_version", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("fully_rated", sa.Boolean(), nullable=False),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        # ── Prediction ───────────────────────────────────────────────────────
        sa.Column("home_xg", sa.Float(), nullable=False),
        sa.Column("away_xg", sa.Float(), nullable=False),
        sa.Column("home_elo", sa.Float(), nullable=True),
        sa.Column("away_elo", sa.Float(), nullable=True),
        sa.Column("home_elo_source", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("away_elo_source", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("home_win_prob", sa.Float(), nullable=False),
        sa.Column("draw_prob", sa.Float(), nullable=False),
        sa.Column("away_win_prob", sa.Float(), nullable=False),
        sa.Column("over_1_5", sa.Float(), nullable=False),
        sa.Column("over_2_5", sa.Float(), nullable=False),
        sa.Column("over_3_5", sa.Float(), nullable=False),
        sa.Column("btts_yes", sa.Float(), nullable=False),
        sa.Column("btts_no", sa.Float(), nullable=False),
        sa.Column("correct_scores", sa.JSON(), nullable=True),
        sa.Column("explanation", sa.JSON(), nullable=True),
        # ── Odds, EV, Kelly ──────────────────────────────────────────────────
        *_float_columns(
            "odds_home", "odds_draw", "odds_away",
            "odds_over_1_5", "odds_under_1_5",
            "odds_over_2_5", "odds_under_2_5",
            "odds_over_3_5", "odds_under_3_5",
            "odds_btts_yes", "odds_btts_no",
            "ev_home", "ev_draw", "ev_away",
            "ev_over_1_5", "ev_under_1_5",
            "ev_over_2_5", "ev_under_2_5",
            "ev_over_3_5", "ev_under_3_5",
            "ev_btts_yes", "ev_btts_no",
            "kelly_home", "kelly_draw", "kelly_away",
            "kelly_over_1_5", "kelly_under_1_5",
            "kelly_over_2_5", "kelly_under_2_5",
            "kelly_over_3_5", "kelly_under_3_5",
            "kelly_btts_yes", "kelly_btts_no",
        ),
        # ── Picks ────────────────────────────────────────────────────────────
        sa.Column("best_bet", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("best_bet_odds", sa.Float(), nullable=True),
        sa.Column("best_bet_prob", sa.Float(), nullable=True),
        sa.Column("best_overall_bet", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("best_overall_odds", sa.Float(), nullable=True),
        sa.Column("best_overall_prob", sa.Float(), nullable=True),
        sa.Column("best_overall_ev", sa.Float(), nullable=True),
        sa.Column("best_overall_kelly", sa.Float(), nullable=True),
        # ── Result ───────────────────────────────────────────────────────────
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.Column("actual_result", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("settled_at", sa.DateTime(), nullable=True),
        sa.Column("ledgered", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # `fixture_id` excludes the date upstream, so a postponed match keeps
        # its identity and updates in place instead of duplicating.
        sa.UniqueConstraint("fixture_id", name="uq_statpitch_fixture_id"),
    )
    op.create_index(
        op.f("ix_statpitch_fixture_fixture_id"), "statpitch_fixture", ["fixture_id"], unique=False
    )
    op.create_index(
        op.f("ix_statpitch_fixture_competition_id"),
        "statpitch_fixture",
        ["competition_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_statpitch_fixture_match_date"), "statpitch_fixture", ["match_date"], unique=False
    )
    op.create_index(
        op.f("ix_statpitch_fixture_ledgered"), "statpitch_fixture", ["ledgered"], unique=False
    )

    op.create_table(
        "statpitch_settled_bet",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fixture_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("competition_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("home_team", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("away_team", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("match_date", sa.Date(), nullable=False),
        sa.Column("settled_at", sa.DateTime(), nullable=False),
        sa.Column("basis", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("selection", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("probability", sa.Float(), nullable=False),
        sa.Column("odds_taken", sa.Float(), nullable=False),
        sa.Column("stake_units", sa.Float(), nullable=False),
        sa.Column("kelly_fraction", sa.Float(), nullable=True),
        sa.Column("won", sa.Boolean(), nullable=False),
        sa.Column("pnl_units", sa.Float(), nullable=False),
        sa.Column("home_score", sa.Integer(), nullable=False),
        sa.Column("away_score", sa.Integer(), nullable=False),
        sa.Column("model_version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # One row per series per fixture, so a re-run cannot double-count a bet.
        sa.UniqueConstraint("fixture_id", "basis", name="uq_statpitch_settled_fixture_basis"),
    )
    op.create_index(
        op.f("ix_statpitch_settled_bet_fixture_id"),
        "statpitch_settled_bet",
        ["fixture_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_statpitch_settled_bet_competition_id"),
        "statpitch_settled_bet",
        ["competition_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_statpitch_settled_bet_match_date"),
        "statpitch_settled_bet",
        ["match_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_statpitch_settled_bet_basis"), "statpitch_settled_bet", ["basis"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema.

    Recreates `statpitch_match_prediction` empty. The dropped rows are not
    recoverable from here — this restores the shape, not the data.
    """
    op.drop_index(op.f("ix_statpitch_settled_bet_basis"), table_name="statpitch_settled_bet")
    op.drop_index(op.f("ix_statpitch_settled_bet_match_date"), table_name="statpitch_settled_bet")
    op.drop_index(
        op.f("ix_statpitch_settled_bet_competition_id"), table_name="statpitch_settled_bet"
    )
    op.drop_index(op.f("ix_statpitch_settled_bet_fixture_id"), table_name="statpitch_settled_bet")
    op.drop_table("statpitch_settled_bet")

    op.drop_index(op.f("ix_statpitch_fixture_ledgered"), table_name="statpitch_fixture")
    op.drop_index(op.f("ix_statpitch_fixture_match_date"), table_name="statpitch_fixture")
    op.drop_index(op.f("ix_statpitch_fixture_competition_id"), table_name="statpitch_fixture")
    op.drop_index(op.f("ix_statpitch_fixture_fixture_id"), table_name="statpitch_fixture")
    op.drop_table("statpitch_fixture")

    op.create_table(
        "statpitch_match_prediction",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_date", sa.Date(), nullable=False),
        sa.Column("commence_time", sa.DateTime(), nullable=True),
        sa.Column("home_team", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("away_team", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("is_neutral", sa.Boolean(), nullable=False),
        sa.Column("home_flag_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("away_flag_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("model_version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("predicted_at", sa.DateTime(), nullable=False),
        sa.Column("home_xg", sa.Float(), nullable=False),
        sa.Column("away_xg", sa.Float(), nullable=False),
        sa.Column("home_elo", sa.Float(), nullable=False),
        sa.Column("away_elo", sa.Float(), nullable=False),
        sa.Column("elo_diff", sa.Float(), nullable=False),
        sa.Column("h2h_games", sa.Integer(), nullable=False),
        sa.Column("h2h_home_wins", sa.Float(), nullable=False),
        sa.Column("home_win_prob", sa.Float(), nullable=False),
        sa.Column("draw_prob", sa.Float(), nullable=False),
        sa.Column("away_win_prob", sa.Float(), nullable=False),
        sa.Column("over_1_5", sa.Float(), nullable=False),
        sa.Column("over_2_5", sa.Float(), nullable=False),
        sa.Column("over_3_5", sa.Float(), nullable=False),
        sa.Column("btts_yes", sa.Float(), nullable=False),
        sa.Column("btts_no", sa.Float(), nullable=False),
        *_float_columns(
            "odds_home", "odds_draw", "odds_away",
            "ev_home", "ev_draw", "ev_away",
            "kelly_home", "kelly_draw", "kelly_away",
            "odds_over_1_5", "odds_under_1_5",
            "odds_over_2_5", "odds_under_2_5",
            "odds_over_3_5", "odds_under_3_5",
            "ev_over_1_5", "ev_under_1_5",
            "ev_over_2_5", "ev_under_2_5",
            "ev_over_3_5", "ev_under_3_5",
            "kelly_over_1_5", "kelly_under_1_5",
            "kelly_over_2_5", "kelly_under_2_5",
            "kelly_over_3_5", "kelly_under_3_5",
            "odds_btts_yes", "odds_btts_no",
            "ev_btts_yes", "ev_btts_no",
            "kelly_btts_yes", "kelly_btts_no",
            "best_overall_ev", "best_overall_kelly",
        ),
        sa.Column("best_bet", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("best_overall_bet", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("actual_result", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_date", "home_team", "away_team", name="uq_match_date_teams"),
    )
    op.create_index(
        op.f("ix_statpitch_match_prediction_match_date"),
        "statpitch_match_prediction",
        ["match_date"],
        unique=False,
    )

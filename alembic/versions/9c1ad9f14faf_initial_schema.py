"""initial_schema

Revision ID: 9c1ad9f14faf
Revises:
Create Date: 2026-06-23 05:17:13.327288

Creates the schema as it stood at this point in history: the portfolio tables
and the StatPitch prediction table, before any of the later revisions added to
them.

This body was originally empty. The tables had been created out of band with
`SQLModel.metadata.create_all()` and the database stamped, so nothing ever
needed it — but that left the chain unable to build a database from scratch,
and the next revision tried to ADD COLUMN to a table that no migration created.
`alembic upgrade head` therefore failed on any fresh database, which meant no
reproducible schema and no clean disaster recovery.

Filling it in is safe for the existing database: it is already stamped well past
this revision, so this never runs there. It only runs on a database starting
from zero, which is precisely the case that was broken.

Deliberately reflects the *original* shape rather than the current one. Later
revisions still add `portfolio_certification.is_main`,
`portfolio_academy.image_url`, the admin tables and the 41 StatPitch columns;
creating any of those here would make those revisions fail on a duplicate.
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c1ad9f14faf"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "portfolio_academy",
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "portfolio_category",
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "portfolio_difficulty_level",
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "portfolio_language",
        sa.Column("code", sqlmodel.sql.sqltypes.AutoString(length=2), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_table(
        "portfolio_project_type",
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "portfolio_tag",
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "statpitch_match_prediction",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_date", sa.Date(), nullable=False),
        sa.Column("home_team", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("away_team", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("is_neutral", sa.Boolean(), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_date", "home_team", "away_team", name="uq_match_date_teams"),
    )
    op.create_table(
        "portfolio_certification",
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("validation_serial", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("academy_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["academy_id"], ["portfolio_academy.id"]),
        sa.ForeignKeyConstraint(["category_id"], ["portfolio_category.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "portfolio_course",
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("academy_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["academy_id"], ["portfolio_academy.id"]),
        sa.ForeignKeyConstraint(["category_id"], ["portfolio_category.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "portfolio_project",
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("is_main", sa.Boolean(), nullable=False),
        sa.Column("image_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("git_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("deploy_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("project_type_id", sa.Integer(), nullable=False),
        sa.Column("difficulty_level_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["project_type_id"], ["portfolio_project_type.id"]),
        sa.ForeignKeyConstraint(["difficulty_level_id"], ["portfolio_difficulty_level.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "portfolio_certification_tag",
        sa.Column("certification_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["certification_id"], ["portfolio_certification.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tag_id"], ["portfolio_tag.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("certification_id", "tag_id"),
    )
    op.create_table(
        "portfolio_certification_translation",
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("language_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("certification_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["language_code"], ["portfolio_language.code"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["certification_id"], ["portfolio_certification.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("language_code", "certification_id"),
    )
    op.create_table(
        "portfolio_course_tag",
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["portfolio_course.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["portfolio_tag.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("course_id", "tag_id"),
    )
    op.create_table(
        "portfolio_course_translation",
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("language_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["language_code"], ["portfolio_language.code"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["course_id"], ["portfolio_course.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("language_code", "course_id"),
    )
    op.create_table(
        "portfolio_project_tag",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["portfolio_project.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["portfolio_tag.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "tag_id"),
    )
    op.create_table(
        "portfolio_project_translation",
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("language_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["language_code"], ["portfolio_language.code"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["portfolio_project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("language_code", "project_id"),
    )

    op.create_index(op.f("ix_portfolio_academy_name"), "portfolio_academy", ["name"], unique=False)
    op.create_index(op.f("ix_portfolio_category_name"), "portfolio_category", ["name"], unique=True)
    op.create_index(
        op.f("ix_portfolio_difficulty_level_name"),
        "portfolio_difficulty_level",
        ["name"],
        unique=True,
    )
    op.create_index(
        op.f("ix_portfolio_project_type_name"), "portfolio_project_type", ["name"], unique=True
    )
    op.create_index(op.f("ix_portfolio_tag_name"), "portfolio_tag", ["name"], unique=True)
    op.create_index(
        op.f("ix_statpitch_match_prediction_match_date"),
        "statpitch_match_prediction",
        ["match_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_portfolio_project_is_main"), "portfolio_project", ["is_main"], unique=False
    )
    op.create_index(op.f("ix_portfolio_project_year"), "portfolio_project", ["year"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("portfolio_project_translation")
    op.drop_table("portfolio_project_tag")
    op.drop_table("portfolio_course_translation")
    op.drop_table("portfolio_course_tag")
    op.drop_table("portfolio_certification_translation")
    op.drop_table("portfolio_certification_tag")
    op.drop_index(op.f("ix_portfolio_project_is_main"), table_name="portfolio_project")
    op.drop_index(op.f("ix_portfolio_project_year"), table_name="portfolio_project")
    op.drop_table("portfolio_project")
    op.drop_table("portfolio_course")
    op.drop_table("portfolio_certification")
    op.drop_index(
        op.f("ix_statpitch_match_prediction_match_date"), table_name="statpitch_match_prediction"
    )
    op.drop_table("statpitch_match_prediction")
    op.drop_index(op.f("ix_portfolio_tag_name"), table_name="portfolio_tag")
    op.drop_table("portfolio_tag")
    op.drop_index(op.f("ix_portfolio_project_type_name"), table_name="portfolio_project_type")
    op.drop_table("portfolio_project_type")
    op.drop_table("portfolio_language")
    op.drop_index(
        op.f("ix_portfolio_difficulty_level_name"), table_name="portfolio_difficulty_level"
    )
    op.drop_table("portfolio_difficulty_level")
    op.drop_index(op.f("ix_portfolio_category_name"), table_name="portfolio_category")
    op.drop_table("portfolio_category")
    op.drop_index(op.f("ix_portfolio_academy_name"), table_name="portfolio_academy")
    op.drop_table("portfolio_academy")

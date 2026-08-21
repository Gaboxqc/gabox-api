"""The migration chain must be able to build a database from nothing.

It could not, for a long time: the initial revision's `upgrade()` was an empty
`pass`, because the tables had been created out of band with
`SQLModel.metadata.create_all()` and the database stamped. The next revision then
tried to ADD COLUMN to a table no migration created, so `alembic upgrade head`
failed on any fresh database — no reproducible schema, and no clean disaster
recovery.

This test is the guard. It runs the real chain against a throwaway database and
compares the result to the ORM, so a future migration that only works against an
already-populated database fails here instead of at recovery time.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from sqlmodel import SQLModel

# Importing these registers every table on the metadata the chain is compared to.
import api.core.auth.models  # noqa: F401
import api.portfolio.models  # noqa: F401
import api.statpitch.accounts  # noqa: F401
import api.statpitch.accounts.keys  # noqa: F401
import api.statpitch.models  # noqa: F401
import api.statpitch.motd  # noqa: F401
import api.statpitch.quota  # noqa: F401
import api.statpitch.teams  # noqa: F401
from alembic import command

REPO_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config(database_url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(name="migrated")
def migrated_fixture(tmp_path, monkeypatch):
    """A database built purely by `alembic upgrade head`."""
    db_file = tmp_path / "migrated.db"
    url = f"sqlite:///{db_file}"
    # alembic/env.py reads DATABASE_URL directly rather than through settings.
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(_alembic_config(url), "head")
    return db_file


def _tables(db_file: Path) -> set[str]:
    with sqlite3.connect(db_file) as conn:
        return {
            row[0]
            for row in conn.execute(
                "select name from sqlite_master " "where type='table' and name != 'alembic_version'"
            )
        }


def test_chain_builds_a_database_from_scratch(migrated):
    assert _tables(migrated), "upgrade head produced no tables"


def test_every_model_table_exists(migrated):
    assert set(SQLModel.metadata.tables) - _tables(migrated) == set()


def test_no_table_exists_that_the_models_do_not_define(migrated):
    assert _tables(migrated) - set(SQLModel.metadata.tables) == set()


def test_columns_match_the_models(migrated):
    """Catches a migration that creates a table with the wrong shape."""
    drift = {}
    with sqlite3.connect(migrated) as conn:
        for name, table in SQLModel.metadata.tables.items():
            found = {row[1] for row in conn.execute(f"PRAGMA table_info({name})")}
            expected = {column.name for column in table.columns}
            if found != expected:
                drift[name] = {
                    "missing": sorted(expected - found),
                    "extra": sorted(found - expected),
                }
    assert drift == {}


def test_nullability_matches_the_models(migrated):
    mismatches = []
    with sqlite3.connect(migrated) as conn:
        for name, table in SQLModel.metadata.tables.items():
            nullable = {row[1]: not row[3] for row in conn.execute(f"PRAGMA table_info({name})")}
            for column in table.columns:
                if column.name in nullable and nullable[column.name] != column.nullable:
                    mismatches.append(f"{name}.{column.name}")
    assert mismatches == []


def test_chain_rolls_all_the_way_back(tmp_path, monkeypatch):
    """A downgrade path that does not reach base means the schema cannot be
    rebuilt, which is half of what recovery needs."""
    db_file = tmp_path / "roundtrip.db"
    url = f"sqlite:///{db_file}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = _alembic_config(url)

    command.upgrade(config, "head")
    command.downgrade(config, "base")

    assert _tables(db_file) == set()

"""Checks that the test database is reachable and migrations apply cleanly."""

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.config import get_settings
from app.db import engine


def test_uses_test_database():
    assert get_settings().database_url == get_settings().test_database_url


def test_database_is_reachable():
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1


def test_migrations_upgrade_and_downgrade():
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", get_settings().test_database_url)

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")

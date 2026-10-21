"""Checks that the database, migrations and Celery are wired up correctly."""

import pytest
from django.core.management import call_command
from django.db import connection

from config.celery import app as celery_app


@pytest.mark.django_db
def test_database_is_reachable() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        assert cursor.fetchone() == (1,)


@pytest.mark.django_db
def test_no_missing_migrations() -> None:
    """Fails if a model changed without a matching migration."""
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)


def test_celery_reads_django_settings() -> None:
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.worker_prefetch_multiplier == 1

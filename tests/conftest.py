"""Shared test setup.

Tests must never touch the development database, so DATABASE_URL is pointed at
TEST_DATABASE_URL here, before any app module creates its engine.
"""

import os

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings

os.environ["DATABASE_URL"] = Settings().test_database_url
get_settings.cache_clear()

from app.main import app  # noqa: E402 — must import after the env override


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)

"""Application settings, loaded from environment variables and `.env`."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://flaky:flaky@localhost:5432/flaky_tracker"
    test_database_url: str = "postgresql+psycopg://flaky:flaky@localhost:5432/flaky_tracker_test"

    # GitHub App — empty until the App is registered (Week 0).
    github_app_client_id: str = ""
    github_app_private_key_path: Path = Path("github-app.private-key.pem")
    github_webhook_secret: SecretStr = SecretStr("")

    # LLM explanations are skipped when no key is set.
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""

    flaky_window_days: int = 30
    flaky_min_events: int = 1

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()

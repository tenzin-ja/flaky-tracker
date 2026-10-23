from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Shared pieces that don't belong to a feature app (health checks for now)."""

    name = "core"

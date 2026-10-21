# Load the Celery app whenever Django starts, so tasks can be queued from views.
from config.celery import app as celery_app

__all__ = ("celery_app",)

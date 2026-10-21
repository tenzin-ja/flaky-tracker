"""Celery application.

Start a worker with:  celery -A config worker --loglevel=info
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("flaky_tracker")

# Read every Django setting that starts with CELERY_ (e.g. CELERY_BROKER_URL).
app.config_from_object("django.conf:settings", namespace="CELERY")

# Find tasks.py in every installed Django app.
app.autodiscover_tasks()

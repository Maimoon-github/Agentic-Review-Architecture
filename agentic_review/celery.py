"""
Celery application configuration for agentic_review.
"""

import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'agentic_review.settings')

app = Celery('agentic_review')

# Read config from Django settings, using the CELERY_ namespace
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks from all installed apps
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Debug utility task."""
    print(f'Request: {self.request!r}')

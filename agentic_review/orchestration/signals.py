"""
Django signals for the orchestration app.

Listens for PipelineRun status transitions and logs them to the console,
providing a passive audit trail without any in-memory coupling.
"""

import logging
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger('orchestration')


@receiver(post_save, sender='orchestration.PipelineRun')
def log_pipeline_status_change(sender, instance, created, **kwargs):
    """
    Log every PipelineRun save (including status transitions) to the console.

    On creation, logs the PENDING initialisation.
    On subsequent saves, logs the current status to track transitions.
    """
    if created:
        logger.info(
            "[PIPELINE STATUS] Pipeline %s CREATED | task=%.60s",
            instance.id, instance.task_description,
        )
    else:
        logger.info(
            "[PIPELINE STATUS] Pipeline %s → %s | iteration=%d",
            instance.id, instance.status, instance.iteration_count,
        )

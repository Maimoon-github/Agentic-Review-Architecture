from django.apps import AppConfig


class OrchestrationConfig(AppConfig):
    """Django app config for the orchestration multi-agent pipeline."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'agentic_review.orchestration'
    label = 'orchestration'

    def ready(self):
        """Import signals when the app is ready."""
        import agentic_review.orchestration.signals  # noqa: F401

from django.apps import AppConfig


class OrchestrationConfig(AppConfig):
    """Django app config for the orchestration multi-agent pipeline."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'orchestration'

    def ready(self):
        """Import signals when the app is ready."""
        import orchestration.signals  # noqa: F401

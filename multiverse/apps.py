from django.apps import AppConfig


class MultiverseConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'multiverse'
    verbose_name = 'Multiverse'

    def ready(self):
        # Importing the module is what registers the checks. Done here rather
        # than at import time so they are registered exactly once, after the
        # app registry is populated.
        from multiverse import checks  # noqa: F401

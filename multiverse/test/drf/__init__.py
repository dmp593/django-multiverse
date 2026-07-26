"""Test helpers for projects using Django REST Framework."""


def __require_djangorestframework() -> None:
    try:
        import rest_framework  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            'djangorestframework is not installed. Run '
            '`pip install django-multiverse[drf]` or '
            '`pip install djangorestframework`, and add "rest_framework" to '
            'your INSTALLED_APPS.'
        ) from exc


# Checked before the submodule imports below, so a missing dependency produces
# the message above rather than an opaque ImportError from deep inside DRF.
__require_djangorestframework()

from multiverse.test.drf.cases import TenantAPITestCase  # noqa: E402
from multiverse.test.drf.client import TenantAPIClient  # noqa: E402

__all__ = [
    'TenantAPITestCase',
    'TenantAPIClient',
]

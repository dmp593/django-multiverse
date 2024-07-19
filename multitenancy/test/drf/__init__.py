def __require_djangorestframework() -> list[str]:
    try:
        import rest_framework
    except ImportError as e:
        raise ImportError(
                'djangorestframework not installed. '
                'run `pip install django-multiverse[drf]` or `pip install djangorestframework`'
                'and add "rest_framework" to your INSTALLED_APPS') \
            from e


__require_djangorestframework()


from multiverse.test.drf.cases import TenantAPITestCase


__all__ = [
    'TenantAPITestCase'
]

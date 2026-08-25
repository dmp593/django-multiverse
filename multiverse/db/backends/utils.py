"""
Backwards-compatible settings accessors.

These were the package's original settings API. They now delegate to
:mod:`multiverse.conf`, which is the single source of truth.

They used to be decorated with ``functools.cache``. That froze the first value
ever seen: because the cache key was derived from settings the package itself
mutated at runtime, the accessors returned stale answers *and* silently defeated
``override_settings`` in every downstream test suite. Caching a ``getattr``
bought nothing and cost correctness.
"""

from __future__ import annotations

from django.conf import settings

from multiverse.conf import (
    DEFAULT_TENANT_DATABASE_ALIAS,
    multiverse_settings,
)


def get_tenant_database_alias(
    or_default: str = DEFAULT_TENANT_DATABASE_ALIAS,
) -> str:
    """Alias in ``DATABASES`` used as the template for tenant connections."""
    return getattr(settings, 'TENANT_DATABASE_ALIAS', or_default)


def get_tenant_database_name(or_default: str | None = None) -> str | None:
    """Database used by the tenant alias when no tenant is active."""
    return multiverse_settings.tenant_database_name or or_default

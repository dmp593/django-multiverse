"""
Whether the package is running inside a test suite.

This flag does **not** disable routing. It stops the connection registry from
deriving new per-tenant aliases, so every query stays inside the databases
Django's test runner created and rolls back. Deriving aliases during a test would
reach real, un-sandboxed databases.

Earlier releases used this flag to send *every* model to ``default``, which meant
a downstream suite ran with routing switched off entirely: misrouted apps,
broken relations and cross-tenant leaks were all invisible in tests and appeared
only in production.
"""

from __future__ import annotations

from django.conf import settings

from multiverse.conf import multiverse_settings

SETTINGS_TESTING_KEY = 'TESTING'


def is_test_environment() -> bool:
    return multiverse_settings.testing


def set_test_environment(testing: bool) -> None:
    setattr(settings, SETTINGS_TESTING_KEY, testing)

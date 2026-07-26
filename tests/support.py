"""Shared helpers for the test suite."""

from __future__ import annotations

from django.test import override_settings

from multiverse.db.connections import tenant_connections


class DerivesTenantAliases:
    """
    Mixin for tests that need real per-tenant alias derivation.

    Derivation is off during tests (``TESTING = True``) so that queries cannot
    escape into real databases. A handful of tests exist precisely to prove
    derivation works, so they switch it back on — and then have to clean up.

    Cleanup is not optional. Django's ``SimpleTestCase`` snapshots the list of
    connection aliases at class setup and walks the *live* list again at
    teardown, expecting to find each one wrapped. An alias that appears in
    between makes teardown fail with a confusing ``'function' object has no
    attribute 'wrapped'``. Anything registered during a test is therefore
    unregistered before it ends.
    """

    def setUp(self):
        super().setUp()

        self._derivation_enabled = override_settings(TESTING=False)
        self._derivation_enabled.enable()
        self.addCleanup(self._derivation_enabled.disable)

    def alias_for(self, database_name: str) -> str:
        """Derive an alias and schedule its removal."""
        self.addCleanup(tenant_connections.unregister, database_name)

        return tenant_connections.alias_for(database_name)

    def forget_alias(self, database_name: str) -> None:
        """Schedule removal of an alias derived indirectly, inside library code."""
        self.addCleanup(tenant_connections.unregister, database_name)

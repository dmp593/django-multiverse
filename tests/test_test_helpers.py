"""
The helpers this package ships for *other* people's test suites.

These had no coverage, which is how `TenantTestCase` came to be broken under
Django's default in-memory SQLite: the tenant's database name defaulted to the
connection's substituted NAME, which for in-memory SQLite is the URI
``file:memorydb_tenant?mode=memory&cache=shared`` — not a storable database name.

A helper nobody tests is a helper that breaks for everybody at once.
"""

from __future__ import annotations

from django.db import DEFAULT_DB_ALIAS
from django.test import SimpleTestCase

from multiverse.awareness import get_current_tenant
from multiverse.models import Tenant
from multiverse.test import TenantClient, TenantTestCase
from tests.apps.tenantapp.models import Invoice


class DefaultTenantTestCaseTests(TenantTestCase):
    """The zero-configuration path: subclass it and go."""

    def test_a_tenant_exists(self):
        self.assertIsNotNone(self.tenant)
        self.assertEqual(self.tenant.subdomain, 'test')

    def test_the_tenant_database_name_is_valid(self):
        """It has to survive the model validators, like any other tenant."""
        self.tenant.full_clean()

    def test_the_tenant_is_active_during_the_test(self):
        self.assertEqual(get_current_tenant(), self.tenant)

    def test_the_client_is_tenant_aware(self):
        self.assertIsInstance(self.client, TenantClient)
        self.assertEqual(self.client.tenant, self.tenant)

    def test_routing_is_live_rather_than_disabled(self):
        """
        The point of the 2.0 change: a downstream suite used to run with every
        model routed to `default`, so routing bugs were invisible in tests.
        """
        invoice = Invoice.objects.create(reference='INV-1')

        self.assertNotEqual(invoice._state.db, DEFAULT_DB_ALIAS)

    def test_the_tenant_registry_still_resolves_from_default(self):
        self.assertEqual(
            Tenant.objects.get(pk=self.tenant.pk)._state.db, DEFAULT_DB_ALIAS
        )

    def test_requests_reach_the_tenant(self):
        response = self.client.get('/whoami/')

        self.assertEqual(response.json()['tenant'], 'test')


class ConfiguredTenantTestCaseTests(TenantTestCase):
    tenant_subdomain = 'acme'
    tenant_database_name = 'acme_db'

    def test_declared_configuration_is_honoured(self):
        self.assertEqual(self.tenant.subdomain, 'acme')
        self.assertEqual(self.tenant.database_name, 'acme_db')

    def test_the_subdomain_is_reachable(self):
        response = self.client.get('/whoami/')

        self.assertEqual(response.json()['tenant'], 'acme')


class TenantIsReleasedTests(SimpleTestCase):
    def test_no_tenant_leaks_out_of_a_tenant_test_case(self):
        """
        `TenantTestCase` releases the tenant in `_post_teardown`. If it did not,
        the leak would surface in whichever unrelated test happened to run next.
        """
        self.assertIsNone(get_current_tenant())


class DeprecatedModuleAliasTests(SimpleTestCase):
    def test_the_misspelled_module_still_works_and_says_so(self):
        """
        `multiverse.test.utls` was published with the typo, so it keeps working
        — it just tells you where the module went.
        """
        import importlib
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            module = importlib.reload(importlib.import_module('multiverse.test.utls'))

        self.assertTrue(callable(module.is_test_environment))
        self.assertTrue(
            any(issubclass(w.category, DeprecationWarning) for w in caught)
        )

"""
Which database ``migrate`` is pointed at.

This was the bug that left every newly created tenant empty: ``create_tenant``
provisioned the new database and then migrated whichever database the shared
tenant alias happened to be pointing at — normally the base one.

The alias passed to ``migrate`` is the whole property, so it is asserted
directly. Doing it end to end would need a connection alias created during the
test, which Django's test cases refuse to query.
"""

from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase

from multiverse.awareness import tenant_context
from multiverse.db.connections import ALIAS_SEPARATOR, tenant_connections
from multiverse.models import Tenant
from multiverse.utils import migrate_tenant_database
from tests.support import DerivesTenantAliases


class MigrationTargetTests(DerivesTenantAliases, SimpleTestCase):
    def setUp(self):
        super().setUp()

        self.tenant = Tenant(subdomain='acme', database_name='db_acme')
        self.forget_alias('db_acme')

    def _migrate_target(self, *args, **kwargs) -> str:
        with mock.patch('multiverse.utils.call_command') as call_command:
            migrate_tenant_database(*args, **kwargs)

        return call_command.call_args.kwargs['database']

    def test_an_explicit_tenant_is_migrated_on_its_own_alias(self):
        target = self._migrate_target(self.tenant)

        self.assertEqual(
            target, f'{tenant_connections.base_alias}{ALIAS_SEPARATOR}db_acme'
        )

    def test_without_a_tenant_the_active_one_is_migrated(self):
        with tenant_context(self.tenant):
            target = self._migrate_target()

        self.assertIn('db_acme', target)

    def test_with_no_tenant_at_all_the_base_alias_is_migrated(self):
        target = self._migrate_target()

        self.assertEqual(target, tenant_connections.base_alias)

    def test_migrate_options_are_passed_through(self):
        with mock.patch('multiverse.utils.call_command') as call_command:
            migrate_tenant_database(self.tenant, verbosity=0, interactive=False)

        self.assertEqual(call_command.call_args.kwargs['verbosity'], 0)
        self.assertFalse(call_command.call_args.kwargs['interactive'])

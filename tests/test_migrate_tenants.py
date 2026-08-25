"""
Fleet migration.

`migrate` handles the system database. This covers the other N databases, which
is the operation you perform every time an app in `TENANT_APPS` gains a
migration — and the one that has no equivalent in single-database Django.
"""

from __future__ import annotations

from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TransactionTestCase

from multiverse.awareness import get_current_tenant
from multiverse.models import Tenant
from tests.support import DerivesTenantAliases


class MigrateTenantsTests(DerivesTenantAliases, TransactionTestCase):
    """
    Real per-tenant aliases are enabled here. Under the usual ``TESTING = True``
    every tenant resolves to the one base alias, which would make "each tenant
    got its own database" unobservable — the assertion that matters most.
    """

    databases = '__all__'

    def setUp(self):
        super().setUp()

        for name in ('acme', 'globex'):
            self.forget_alias(name)

        self.acme = Tenant.objects.create(subdomain='acme', database_name='acme')
        self.globex = Tenant.objects.create(subdomain='globex', database_name='globex')

    def _migrated(self, *args, **kwargs):
        """Run the command with `migrate` stubbed, returning the calls it made."""
        with mock.patch('multiverse.utils.call_command') as call:
            call_command('migrate_tenants', *args, verbosity=0, **kwargs)

        return call.call_args_list

    def _aliases(self, calls) -> list[str]:
        return [call.kwargs['database'] for call in calls]

    def test_every_tenant_is_migrated(self):
        calls = self._migrated()

        self.assertEqual(len(calls), 2)
        aliases = self._aliases(calls)
        self.assertTrue(any('acme' in alias for alias in aliases))
        self.assertTrue(any('globex' in alias for alias in aliases))

    def test_each_tenant_gets_its_own_database(self):
        """The whole point: N databases, not one repeated N times."""
        aliases = self._aliases(self._migrated())

        self.assertEqual(len(set(aliases)), 2)

    def test_tenants_are_migrated_in_a_stable_order(self):
        """
        A fleet run that dies half way must be resumable, which means the order
        cannot depend on insertion or on the database's mood.
        """
        first = self._aliases(self._migrated())
        second = self._aliases(self._migrated())

        self.assertEqual(first, second)

    def test_a_single_tenant_can_be_targeted(self):
        aliases = self._aliases(self._migrated('--tenant', 'acme'))

        self.assertEqual(len(aliases), 1)
        self.assertIn('acme', aliases[0])

    def test_tenants_can_be_targeted_repeatedly_to_shard_a_fleet(self):
        aliases = self._aliases(
            self._migrated('--tenant', 'acme', '--tenant', 'globex')
        )

        self.assertEqual(len(aliases), 2)

    def test_a_tenant_can_be_targeted_by_database_name(self):
        aliases = self._aliases(self._migrated('--tenant', 'globex'))

        self.assertIn('globex', aliases[0])

    def test_an_unknown_tenant_is_reported_clearly(self):
        with self.assertRaises(CommandError) as caught:
            self._migrated('--tenant', 'nobody')

        self.assertIn('No tenant matches', str(caught.exception))

    def test_soft_deleted_tenants_are_skipped(self):
        """
        A decommissioned tenant should not be dragged along by every subsequent
        schema change.
        """
        self.globex.delete()

        aliases = self._aliases(self._migrated())

        self.assertEqual(len(aliases), 1)
        self.assertIn('acme', aliases[0])

    def test_soft_deleted_tenants_can_be_included(self):
        self.globex.delete()

        self.assertEqual(len(self._migrated('--include-deleted')), 2)

    def test_migrate_arguments_are_forwarded(self):
        calls = self._migrated('tenantapp', '0001_initial')

        self.assertEqual(calls[0].args, ('migrate', 'tenantapp', '0001_initial'))

    def test_migrate_flags_are_forwarded(self):
        calls = self._migrated('--fake')

        self.assertTrue(calls[0].kwargs['fake'])

    def test_no_tenants_is_not_an_error(self):
        Tenant.objects.all().delete(hard=True)

        self.assertEqual(self._migrated(), [])


class MigrateTenantsFailureTests(DerivesTenantAliases, TransactionTestCase):
    databases = '__all__'

    def setUp(self):
        super().setUp()

        for subdomain in ('acme', 'globex', 'initech'):
            self.forget_alias(subdomain)
            Tenant.objects.create(subdomain=subdomain, database_name=subdomain)

    def _failing_on(self, subdomain: str):
        """Stub `migrate` so that exactly one tenant blows up."""
        def migrate(*args, **kwargs):
            if subdomain in kwargs.get('database', ''):
                raise RuntimeError(f'{subdomain} exploded')

        return mock.patch('multiverse.utils.call_command', side_effect=migrate)

    def test_the_run_stops_at_the_first_failure_by_default(self):
        """
        Half-applying a broken migration across a fleet is worse than stopping:
        migrations are idempotent, so a fixed re-run costs nothing.
        """
        with self._failing_on('globex') as call:
            with self.assertRaises(CommandError):
                call_command('migrate_tenants', verbosity=0)

        attempted = [c.kwargs['database'] for c in call.call_args_list]
        self.assertEqual(len(attempted), 2)
        self.assertFalse(any('initech' in alias for alias in attempted))

    def test_keep_going_attempts_every_tenant(self):
        with self._failing_on('globex') as call:
            with self.assertRaises(CommandError):
                call_command('migrate_tenants', '--keep-going', verbosity=0)

        self.assertEqual(len(call.call_args_list), 3)

    def test_keep_going_still_exits_non_zero(self):
        """A partial success is a failure: CI must not read it as green."""
        with self._failing_on('globex'):
            with self.assertRaises(CommandError) as caught:
                call_command('migrate_tenants', '--keep-going', verbosity=0)

        self.assertIn('1 of 3', str(caught.exception))

    def test_the_tenant_is_released_after_a_failure(self):
        with self._failing_on('acme'):
            with self.assertRaises(CommandError):
                call_command('migrate_tenants', verbosity=0)

        self.assertIsNone(get_current_tenant())


class TenantContextDuringMigrationTests(DerivesTenantAliases, TransactionTestCase):
    databases = '__all__'

    def setUp(self):
        super().setUp()
        self.forget_alias('acme')

    def test_the_tenant_is_active_while_its_migrations_run(self):
        """
        Data migrations using RunPython need to know which customer they are
        rewriting, which they can only do if the tenant is active.
        """
        Tenant.objects.create(subdomain='acme', database_name='acme')
        seen = []

        with mock.patch(
            'multiverse.utils.call_command',
            side_effect=lambda *a, **kw: seen.append(get_current_tenant()),
        ):
            call_command('migrate_tenants', verbosity=0)

        self.assertEqual([tenant.subdomain for tenant in seen], ['acme'])

    def test_no_tenant_leaks_after_the_run(self):
        Tenant.objects.create(subdomain='acme', database_name='acme')

        with mock.patch('multiverse.utils.call_command'):
            call_command('migrate_tenants', verbosity=0)

        self.assertIsNone(get_current_tenant())

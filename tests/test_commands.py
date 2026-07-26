"""
Management commands, end to end.

Real SQLite files in a temporary directory rather than mocks, because the bug
that mattered most here — the argument parser rejecting the command's own
documented invocation — is invisible to a mock.

Which database ``migrate`` targets is covered separately in
``test_migration_targeting.py``. Proving that end to end would need a connection
alias created *during* the test, and Django's test cases refuse queries against
any alias that did not exist when the class was set up.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import TransactionTestCase, override_settings

from multiverse.models import Tenant


class ProvisioningTestCase(TransactionTestCase):
    """Shared setup: a temporary directory for tenant database files."""

    databases = '__all__'

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup_directory)

        override = override_settings(TENANT_DATABASE_DIRECTORY=self.directory)
        override.enable()
        self.addCleanup(override.disable)

    def _cleanup_directory(self):
        for path in sorted(self.directory.glob('*')):
            path.unlink()
        self.directory.rmdir()


class TenantCreationTests(ProvisioningTestCase):
    def test_the_documented_invocation_parses(self):
        """
        `--create-database --migrate` is exactly what the README told people to
        run. Declared as `type=bool`, argparse rejected it with "expected one
        argument", and no value could express false either.
        """
        call_command(
            'create_tenant', 'acme', '--database-name', 'acme',
            '--create-database', '--migrate', verbosity=0,
        )

        self.assertTrue(Tenant.objects.filter(subdomain='acme').exists())

    def test_the_database_file_is_created_inside_the_tenant_directory(self):
        call_command('create_tenant', 'acme', verbosity=0)

        self.assertTrue((self.directory / 'acme').exists())

    def test_flags_can_be_negated(self):
        call_command('create_tenant', 'acme', '--no-create-database', verbosity=0)

        self.assertFalse((self.directory / 'acme').exists())
        self.assertTrue(Tenant.objects.filter(subdomain='acme').exists())

    def test_the_database_name_defaults_to_the_subdomain(self):
        call_command('create_tenant', 'acme', verbosity=0)

        self.assertEqual(Tenant.objects.get(subdomain='acme').database_name, 'acme')

    def test_an_invalid_subdomain_is_reported_clearly(self):
        with self.assertRaises(CommandError):
            call_command('create_tenant', 'Not A Subdomain', verbosity=0)

        self.assertFalse(Tenant.objects_with_deleted.exists())

    def test_a_hostile_database_name_is_refused(self):
        with self.assertRaises(CommandError):
            call_command(
                'create_tenant', 'acme',
                '--database-name', '../../escape', verbosity=0,
            )

    def test_creating_a_duplicate_tenant_is_reported_clearly(self):
        call_command('create_tenant', 'acme', verbosity=0)

        with self.assertRaises(CommandError) as caught:
            call_command('create_tenant', 'acme', verbosity=0)

        self.assertIn('already exists', str(caught.exception))

    def test_a_conflicting_database_name_is_reported_clearly(self):
        """
        Both fields are independently unique, so a partial match is a real
        conflict. It used to surface as a raw IntegrityError traceback.
        """
        call_command('create_tenant', 'acme', verbosity=0)

        with self.assertRaises(CommandError) as caught:
            call_command(
                'create_tenant', 'other', '--database-name', 'acme', verbosity=0
            )

        self.assertIn('Conflict', str(caught.exception))

    def test_recreating_a_deleted_tenant_explains_how_to_restore_it(self):
        call_command('create_tenant', 'acme', verbosity=0)
        call_command('destroy_tenant', 'acme', verbosity=0)

        with self.assertRaises(CommandError) as caught:
            call_command('create_tenant', 'acme', verbosity=0)

        self.assertIn('restore', str(caught.exception).lower())


class TenantDestructionTests(ProvisioningTestCase):
    def setUp(self):
        super().setUp()
        call_command('create_tenant', 'acme', verbosity=0)

    def test_the_default_is_a_soft_delete_that_keeps_the_database(self):
        call_command('destroy_tenant', 'acme', verbosity=0)

        self.assertFalse(Tenant.objects.filter(subdomain='acme').exists())
        self.assertTrue(
            Tenant.objects_with_deleted.filter(subdomain='acme').exists()
        )
        self.assertTrue((self.directory / 'acme').exists())

    def test_dropping_the_database_removes_the_file(self):
        call_command(
            'destroy_tenant', 'acme', '--drop-database', '--noinput', verbosity=0
        )

        self.assertFalse((self.directory / 'acme').exists())

    def test_the_row_survives_a_database_drop_so_the_mapping_is_not_lost(self):
        """
        The row is the only record of which database belonged to which customer.
        Deleting it first and then failing to drop — which is what happened while
        the PostgreSQL backend issued invalid SQL — orphaned a database nobody
        could identify afterwards.
        """
        call_command(
            'destroy_tenant', 'acme', '--drop-database', '--noinput', verbosity=0
        )

        tenant = Tenant.objects_with_deleted.get(subdomain='acme')
        self.assertEqual(tenant.database_name, 'acme')

    def test_hard_delete_removes_the_row(self):
        call_command('destroy_tenant', 'acme', '--hard', verbosity=0)

        self.assertFalse(
            Tenant.objects_with_deleted.filter(subdomain='acme').exists()
        )

    def test_a_tenant_can_be_addressed_by_database_name(self):
        call_command('destroy_tenant', 'acme', '--hard', verbosity=0)

        self.assertFalse(Tenant.objects_with_deleted.exists())

    def test_an_unknown_tenant_is_reported_clearly(self):
        with self.assertRaises(CommandError):
            call_command('destroy_tenant', 'nobody', verbosity=0)

"""
Database provisioning and the validation that guards it.

``database_name`` is the one tenant field that leaves the ORM: it becomes a
filesystem path under SQLite and a SQL identifier under PostgreSQL. These tests
cover both layers that constrain it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.core.exceptions import SuspiciousOperation, ValidationError
from django.test import SimpleTestCase, TestCase, override_settings

from multiverse.db.backends.base import get_provisioner
from multiverse.db.backends.sqlite3.utils import SQLiteProvisioner
from multiverse.models import Tenant
from multiverse.validators import validate_database_name, validate_subdomain


class DatabaseNameValidationTests(SimpleTestCase):
    def test_ordinary_names_are_accepted(self):
        for name in ('acme', 'acme_db', 'acme-eu', 'acme.sqlite3', 'db1'):
            with self.subTest(name=name):
                validate_database_name(name)

    def test_traversal_and_separators_are_rejected(self):
        hostile = (
            '../../etc/passwd',
            '..',
            'a/b',
            'a\\b',
            '/absolute',
            'name with spaces',
            'quote"name',
            'semi;colon',
            '',
        )

        for name in hostile:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                validate_database_name(name)

    def test_subdomains_must_be_a_single_dns_label(self):
        validate_subdomain('acme')
        validate_subdomain('acme-eu')

        for value in ('Acme', 'acme.example', '-acme', 'acme-', 'a b', ''):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_subdomain(value)


class TenantModelValidationTests(TestCase):
    databases = '__all__'

    def test_a_hostile_database_name_cannot_be_saved(self):
        """
        Validation runs on save rather than only in forms, so no call site can
        reach a provisioner with a name that escapes the tenant directory.
        """
        with self.assertRaises(ValidationError):
            Tenant.objects.create(subdomain='evil', database_name='../../escape')


class SQLiteProvisioningTests(SimpleTestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup)

        override = override_settings(TENANT_DATABASE_DIRECTORY=self.directory)
        override.enable()
        self.addCleanup(override.disable)

        self.provisioner = SQLiteProvisioner({'ENGINE': 'django.db.backends.sqlite3'})

    def _cleanup(self):
        for path in self.directory.glob('*'):
            path.unlink()
        self.directory.rmdir()

    def test_creating_a_database_makes_a_file_inside_the_directory(self):
        path, created = self.provisioner.create_if_not_exists('acme')

        self.assertTrue(created)
        self.assertTrue(Path(path).exists())
        self.assertEqual(Path(path).parent, self.directory)

    def test_creating_an_existing_database_is_a_no_op(self):
        self.provisioner.create_if_not_exists('acme')
        _, created = self.provisioner.create_if_not_exists('acme')

        self.assertFalse(created)

    def test_dropping_removes_the_file(self):
        path, _ = self.provisioner.create_if_not_exists('acme')

        _, dropped = self.provisioner.drop_if_exists('acme')

        self.assertTrue(dropped)
        self.assertFalse(Path(path).exists())

    def test_dropping_a_missing_database_is_a_no_op(self):
        _, dropped = self.provisioner.drop_if_exists('never_existed')

        self.assertFalse(dropped)

    def test_a_path_outside_the_directory_is_refused(self):
        """
        The sanitisation that would have prevented this was commented out, so a
        tenant name of '../../app/settings.py' was created with touch() and
        removed with unlink().
        """
        with self.assertRaises((SuspiciousOperation, ValidationError)):
            self.provisioner.create_if_not_exists('../escape')

    def test_the_connection_name_is_the_resolved_path(self):
        """
        The provisioner and the connection must agree on which file is meant.
        If they derived it separately, `create_tenant` would create one file and
        Django would open another.
        """
        connection_name = self.provisioner.connection_name('acme')
        path, _ = self.provisioner.create_if_not_exists('acme')

        self.assertEqual(connection_name, path)

    def test_in_memory_databases_are_not_treated_as_paths(self):
        name, created = self.provisioner.create_if_not_exists(':memory:')

        self.assertEqual(name, ':memory:')
        self.assertFalse(created)


class ProvisionerSelectionTests(SimpleTestCase):
    def test_the_sqlite_provisioner_is_selected_for_the_tenant_alias(self):
        self.assertIsInstance(get_provisioner('tenant'), SQLiteProvisioner)

    def test_an_unknown_alias_has_no_provisioner(self):
        self.assertIsNone(get_provisioner('does-not-exist'))

"""
PostgreSQL provisioning, without a PostgreSQL server.

The connection is faked so that the statements this backend composes, and the
credentials it composes them with, are asserted directly. That covers the two
defects that mattered here — a drop that issued ``CREATE DATABASE IF EXISTS``,
and credentials read from the wrong alias — neither of which needs a server to
demonstrate.

What this cannot prove is that PostgreSQL *accepts* the SQL. The
``test-postgres`` CI job runs the whole suite against a real server for that.

Skipped when psycopg is not installed, since it is an optional extra.
"""

from __future__ import annotations

from unittest import mock, skipUnless

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

try:
    import psycopg  # noqa: F401

    PSYCOPG_INSTALLED = True
except ImportError:  # pragma: no cover - exercised only without the extra
    PSYCOPG_INSTALLED = False

if PSYCOPG_INSTALLED:
    from multiverse.db.backends.postgresql.utils import (
        PostgreSQLProvisioner,
        get_provisioning_database_name,
    )


TENANT_CONNECTION_SETTINGS = {
    'ENGINE': 'django.db.backends.postgresql',
    'NAME': 'tenant_base',
    'USER': 'tenant_role',
    'PASSWORD': 'tenant_secret',
    'HOST': 'tenants.internal',
    'PORT': '6432',
}


class FakeCursor:
    """Records every statement, and answers the existence check on demand."""

    def __init__(self, exists: bool):
        self._exists = exists
        self.statements: list[str] = []

    def execute(self, query, params=None):
        self.statements.append(
            query if isinstance(query, str) else query.as_string(None)
        )

    def fetchone(self):
        return (1,) if self._exists else None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@skipUnless(PSYCOPG_INSTALLED, 'psycopg is not installed')
class PostgreSQLProvisionerTests(SimpleTestCase):
    def _run(self, method: str, database_name: str, *, exists: bool):
        cursor = FakeCursor(exists=exists)
        provisioner = PostgreSQLProvisioner(TENANT_CONNECTION_SETTINGS)

        with mock.patch(
            'multiverse.db.backends.postgresql.utils.psycopg.connect',
            return_value=FakeConnection(cursor),
        ) as connect:
            result = getattr(provisioner, method)(database_name)

        return cursor.statements, connect.call_args, result

    def test_dropping_issues_a_drop_and_not_a_create(self):
        """
        The regression that mattered most in this file: the drop path issued
        `CREATE DATABASE IF EXISTS`, which is not valid SQL under any reading,
        so dropping a tenant database always failed.
        """
        statements, _, (_, dropped) = self._run(
            'drop_if_exists', 'acme_db', exists=True
        )

        self.assertTrue(dropped)
        self.assertIn('DROP DATABASE IF EXISTS "acme_db"', statements)
        self.assertFalse(any('CREATE DATABASE' in s for s in statements))

    def test_open_sessions_are_terminated_before_the_drop(self):
        """
        PostgreSQL refuses to drop a database anyone is connected to, and one
        idle worker connection elsewhere in the fleet is enough to block it.
        """
        statements, _, _ = self._run('drop_if_exists', 'acme_db', exists=True)

        terminate = next(
            index for index, s in enumerate(statements)
            if 'pg_terminate_backend' in s
        )
        drop = next(
            index for index, s in enumerate(statements) if 'DROP DATABASE' in s
        )

        self.assertLess(terminate, drop)

    def test_dropping_a_missing_database_issues_no_ddl(self):
        statements, _, (_, dropped) = self._run(
            'drop_if_exists', 'acme_db', exists=False
        )

        self.assertFalse(dropped)
        self.assertFalse(any('DROP DATABASE' in s for s in statements))

    def test_creating_issues_a_create(self):
        statements, _, (_, created) = self._run(
            'create_if_not_exists', 'acme_db', exists=False
        )

        self.assertTrue(created)
        self.assertIn('CREATE DATABASE "acme_db"', statements)

    def test_creating_an_existing_database_issues_no_ddl(self):
        statements, _, (_, created) = self._run(
            'create_if_not_exists', 'acme_db', exists=True
        )

        self.assertFalse(created)
        self.assertFalse(any('CREATE DATABASE' in s for s in statements))

    def test_credentials_come_from_the_tenant_alias(self):
        """
        They were hardcoded to `DATABASES['default']`, which forced every tenant
        database onto the system server under the system role.
        """
        _, connect_call, _ = self._run(
            'create_if_not_exists', 'acme_db', exists=False
        )

        self.assertEqual(connect_call.kwargs['user'], 'tenant_role')
        self.assertEqual(connect_call.kwargs['password'], 'tenant_secret')
        self.assertEqual(connect_call.kwargs['host'], 'tenants.internal')
        self.assertEqual(connect_call.kwargs['port'], '6432')

    def test_ddl_runs_against_the_maintenance_database(self):
        """Server-level DDL cannot be issued from the database it targets."""
        _, connect_call, _ = self._run(
            'create_if_not_exists', 'acme_db', exists=False
        )

        self.assertEqual(connect_call.kwargs['dbname'], 'postgres')
        self.assertNotEqual(connect_call.kwargs['dbname'], 'acme_db')

    def test_ddl_runs_with_autocommit(self):
        """CREATE and DROP DATABASE cannot run inside a transaction."""
        _, connect_call, _ = self._run(
            'create_if_not_exists', 'acme_db', exists=False
        )

        self.assertTrue(connect_call.kwargs['autocommit'])

    def test_missing_connection_keys_do_not_raise(self):
        """
        These were bare `[...]` lookups, so a DATABASES entry without an
        explicit PORT raised KeyError rather than defaulting.
        """
        provisioner = PostgreSQLProvisioner({'ENGINE': 'django.db.backends.postgresql'})

        with mock.patch(
            'multiverse.db.backends.postgresql.utils.psycopg.connect',
            return_value=FakeConnection(FakeCursor(exists=True)),
        ) as connect:
            provisioner.create_if_not_exists('acme_db')

        self.assertIsNone(connect.call_args.kwargs['port'])

    def test_the_identifier_is_quoted(self):
        """
        Database names cannot be parameterised in DDL, so `sql.Identifier` is
        what stands between a tenant name and injection.
        """
        statements, _, _ = self._run(
            'create_if_not_exists', 'weird-name', exists=False
        )

        self.assertIn('CREATE DATABASE "weird-name"', statements)

    def test_a_hostile_name_is_refused_before_any_connection_is_opened(self):
        provisioner = PostgreSQLProvisioner(TENANT_CONNECTION_SETTINGS)

        with mock.patch(
            'multiverse.db.backends.postgresql.utils.psycopg.connect'
        ) as connect:
            with self.assertRaises(ValidationError):
                provisioner.create_if_not_exists('acme"; DROP DATABASE other; --')

        connect.assert_not_called()


@skipUnless(PSYCOPG_INSTALLED, 'psycopg is not installed')
class ProvisioningDatabaseSettingTests(SimpleTestCase):
    def test_it_defaults_to_postgres(self):
        self.assertEqual(get_provisioning_database_name(), 'postgres')

    def test_it_is_configurable(self):
        """Some managed providers do not expose a `postgres` database."""
        with self.settings(TENANT_PROVISIONING_DATABASE='defaultdb'):
            self.assertEqual(get_provisioning_database_name(), 'defaultdb')

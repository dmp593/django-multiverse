"""
PostgreSQL provisioning.

``CREATE DATABASE`` and ``DROP DATABASE`` cannot run inside a transaction and
cannot address the database they are executed from, so this backend opens its
own autocommit connection to a maintenance database rather than borrowing one of
Django's.
"""

from __future__ import annotations

import psycopg
from psycopg import sql

from multiverse.conf import multiverse_settings
from multiverse.db.backends.base import DatabaseProvisioner
from multiverse.validators import validate_database_name


class PostgreSQLProvisioner(DatabaseProvisioner):
    """Provisions tenant databases on a PostgreSQL server."""

    def create_if_not_exists(self, database_name: str) -> tuple[str, bool]:
        validate_database_name(database_name)

        with self._maintenance_connection() as connection:
            with connection.cursor() as cursor:
                if self._exists(cursor, database_name):
                    return database_name, False

                cursor.execute(
                    sql.SQL('CREATE DATABASE {}').format(
                        sql.Identifier(database_name)
                    )
                )

        return database_name, True

    def drop_if_exists(self, database_name: str) -> tuple[str, bool]:
        validate_database_name(database_name)

        with self._maintenance_connection() as connection:
            with connection.cursor() as cursor:
                if not self._exists(cursor, database_name):
                    return database_name, False

                self._terminate_connections(cursor, database_name)

                cursor.execute(
                    sql.SQL('DROP DATABASE IF EXISTS {}').format(
                        sql.Identifier(database_name)
                    )
                )

        return database_name, True

    def _maintenance_connection(self) -> psycopg.Connection:
        """
        Connect to the maintenance database using the *tenant* alias' credentials.

        Reading credentials from the tenant alias rather than from ``default``
        is what allows tenant databases to live on a different server, with a
        different role, than the system database.
        """
        settings = self.connection_settings

        return psycopg.connect(
            dbname=multiverse_settings.provisioning_database_name,
            user=settings.get('USER') or None,
            password=settings.get('PASSWORD') or None,
            host=settings.get('HOST') or None,
            port=settings.get('PORT') or None,
            autocommit=True,
        )

    @staticmethod
    def _exists(cursor, database_name: str) -> bool:
        cursor.execute(
            'SELECT 1 FROM pg_catalog.pg_database WHERE datname = %(datname)s',
            {'datname': database_name},
        )

        return cursor.fetchone() is not None

    @staticmethod
    def _terminate_connections(cursor, database_name: str) -> None:
        """
        Evict every other session from the database so the drop can proceed.

        PostgreSQL refuses to drop a database that anyone is connected to. A
        single idle connection held by a web worker elsewhere in the fleet is
        enough to make ``destroy_tenant`` fail, so those sessions are terminated
        explicitly rather than waiting them out.
        """
        cursor.execute(
            'SELECT pg_terminate_backend(pid) '
            'FROM pg_stat_activity '
            'WHERE datname = %(datname)s AND pid <> pg_backend_pid()',
            {'datname': database_name},
        )


def create_database_if_not_exists(name: str) -> tuple[str, bool]:
    """Backwards-compatible wrapper around :class:`PostgreSQLProvisioner`."""
    return _provisioner().create_if_not_exists(name)


def drop_database_if_exists(name: str) -> tuple[str, bool]:
    """Backwards-compatible wrapper around :class:`PostgreSQLProvisioner`."""
    return _provisioner().drop_if_exists(name)


def _provisioner() -> PostgreSQLProvisioner:
    from django.db import connections

    alias = multiverse_settings.tenant_database_alias
    return PostgreSQLProvisioner(connections.settings.get(alias, {}))

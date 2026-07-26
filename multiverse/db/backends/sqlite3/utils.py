"""
SQLite provisioning.

A SQLite "database name" is a filesystem path, which makes this the one backend
where a tenant record can reach outside the database layer entirely. A
``database_name`` of ``../../app/settings.py`` would have been created by
``touch()`` and removed by ``unlink()``.

Two independent controls prevent that, and neither is trusted to be the only
one: the tenant model rejects hostile names at write time, and every path built
here is resolved and then checked to be inside the configured tenant directory.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings as django_settings
from django.core.exceptions import SuspiciousOperation

from multiverse.conf import multiverse_settings
from multiverse.db.backends.base import DatabaseProvisioner
from multiverse.validators import validate_database_name

#: SQLite's in-memory database. It has no file, exists only for the connection
#: that opened it, and must never be treated as a path.
IN_MEMORY_DATABASE = ':memory:'


def get_tenant_database_directory() -> Path:
    """
    Directory that file-backed tenant databases are confined to.

    ``TENANT_DATABASE_DIRECTORY`` is read here rather than in
    :mod:`multiverse.conf` because it means nothing to a server-based engine.
    Backend-specific settings live with the backend that consumes them, so
    adding an engine never requires touching the engine-neutral core.

    Defaults to ``BASE_DIR`` when the project defines it, otherwise the
    directory holding the base tenant database.
    """
    configured = getattr(django_settings, 'TENANT_DATABASE_DIRECTORY', None)
    if configured:
        return Path(configured).resolve()

    base_dir = getattr(django_settings, 'BASE_DIR', None)
    if base_dir:
        return Path(base_dir).resolve()

    base_database = multiverse_settings.tenant_database_name
    if base_database and base_database != IN_MEMORY_DATABASE:
        return Path(base_database).resolve().parent

    return Path.cwd().resolve()


class SQLiteProvisioner(DatabaseProvisioner):
    """Provisions tenant databases as files inside a confined directory."""

    @property
    def directory(self) -> Path:
        return get_tenant_database_directory()

    def connection_name(self, database_name: str) -> str:
        if self._is_in_memory(database_name):
            return database_name

        return str(self.resolve_path(database_name))

    def resolve_path(self, database_name: str) -> Path:
        """
        Turn a tenant's ``database_name`` into an absolute, confined file path.

        Resolution happens *before* the containment check so that symlinks and
        ``..`` segments are collapsed first — checking the unresolved path would
        be trivially bypassable.
        """
        validate_database_name(database_name)

        directory = self.directory
        candidate = (directory / database_name).resolve()

        if not candidate.is_relative_to(directory):
            raise SuspiciousOperation(
                f'Tenant database "{database_name}" resolves to {candidate}, '
                f'which is outside the tenant database directory {directory}. '
                f'Set TENANT_DATABASE_DIRECTORY if your tenant databases live '
                f'somewhere else.'
            )

        return candidate

    def create_if_not_exists(self, database_name: str) -> tuple[str, bool]:
        if self._is_in_memory(database_name):
            # An in-memory database springs into existence with the connection
            # that opens it, so there is nothing to create.
            return database_name, False

        path = self.resolve_path(database_name)

        if path.exists():
            return str(path), False

        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

        return str(path), True

    def drop_if_exists(self, database_name: str) -> tuple[str, bool]:
        if self._is_in_memory(database_name):
            return database_name, False

        path = self.resolve_path(database_name)

        if not path.exists():
            return str(path), False

        path.unlink()

        return str(path), True

    @staticmethod
    def _is_in_memory(database_name: str) -> bool:
        return database_name == IN_MEMORY_DATABASE or 'mode=memory' in database_name


def create_database_if_not_exists(name: str) -> tuple[str, bool]:
    """Backwards-compatible wrapper around :class:`SQLiteProvisioner`."""
    return _provisioner().create_if_not_exists(name)


def drop_database_if_exists(name: str) -> tuple[str, bool]:
    """Backwards-compatible wrapper around :class:`SQLiteProvisioner`."""
    return _provisioner().drop_if_exists(name)


def _provisioner() -> SQLiteProvisioner:
    from django.db import connections

    alias = multiverse_settings.tenant_database_alias
    return SQLiteProvisioner(connections.settings.get(alias, {}))

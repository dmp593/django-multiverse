"""
The contract every database backend must satisfy to host tenants.

Provisioning — physically creating and dropping databases — is the one operation
Django's ORM does not abstract, so each engine needs its own implementation.
Earlier releases expressed this as a module discovered by string-sniffing the
engine and then poked at with ``hasattr``. That works until it silently does
nothing: a typo'd method name simply meant no database was ever created, with no
error anywhere.

An abstract base class makes the contract explicit and unmissable — a backend
that forgets a method cannot be instantiated at all.
"""

from __future__ import annotations

import abc
from importlib import import_module

from django.core.exceptions import ImproperlyConfigured
from django.db import connections


class DatabaseProvisioner(abc.ABC):
    """Creates and drops the physical databases that back individual tenants."""

    def __init__(self, connection_settings: dict) -> None:
        #: The ``DATABASES`` entry for the tenant alias. Supplies host,
        #: credentials and engine options. Never mutated.
        self.connection_settings = connection_settings

    @abc.abstractmethod
    def create_if_not_exists(self, database_name: str) -> tuple[str, bool]:
        """
        Ensure ``database_name`` exists.

        Returns ``(name, created)`` where ``created`` distinguishes "I made it"
        from "it was already there", so callers can report accurately instead of
        guessing.
        """

    @abc.abstractmethod
    def drop_if_exists(self, database_name: str) -> tuple[str, bool]:
        """
        Ensure ``database_name`` does not exist.

        Returns ``(name, dropped)``.
        """

    def connection_name(self, database_name: str) -> str:
        """
        Translate a tenant's ``database_name`` into a ``DATABASES['NAME']`` value.

        Identical to the input for engines that address databases by name. File
        backed engines override this to return a resolved path, so that the
        database the provisioner creates and the database Django connects to are
        guaranteed to be the same file.
        """
        return database_name


#: Maps a marker found in ``ENGINE`` to the provisioner that handles it.
#:
#: Matching is by substring rather than equality on purpose: engines such as
#: ``django.contrib.gis.db.backends.postgis`` and third-party wrappers extend the
#: core backends and provision identically.
_PROVISIONERS: dict[str, str] = {
    'sqlite3': 'multiverse.db.backends.sqlite3.utils.SQLiteProvisioner',
    'postgresql': 'multiverse.db.backends.postgresql.utils.PostgreSQLProvisioner',
    'postgis': 'multiverse.db.backends.postgresql.utils.PostgreSQLProvisioner',
}


def register_provisioner(engine_marker: str, provisioner_path: str) -> None:
    """
    Teach the package how to provision another engine.

    ``provisioner_path`` is a dotted path to a :class:`DatabaseProvisioner`
    subclass, imported lazily so that registering a backend never forces its
    driver to be installed.
    """
    _PROVISIONERS[engine_marker] = provisioner_path


def get_provisioner(alias: str) -> DatabaseProvisioner | None:
    """
    Build the provisioner for a connection alias, or ``None`` if the engine has
    no provisioning support.

    ``None`` is a legitimate answer, not an error: an engine may host tenant
    databases that are created out-of-band by a DBA or by infrastructure code.
    Callers decide whether that is acceptable for what they are doing.
    """
    try:
        connection_settings = connections.settings[alias]
    except KeyError:
        return None

    engine = connection_settings.get('ENGINE', '')

    for marker, provisioner_path in _PROVISIONERS.items():
        if marker in engine:
            return _import_provisioner(provisioner_path)(connection_settings)

    return None


def _import_provisioner(provisioner_path: str) -> type[DatabaseProvisioner]:
    module_path, class_name = provisioner_path.rsplit('.', 1)

    try:
        provisioner_class = getattr(import_module(module_path), class_name)
    except (ImportError, AttributeError) as exc:
        raise ImproperlyConfigured(
            f'Could not import database provisioner "{provisioner_path}".'
        ) from exc

    if not issubclass(provisioner_class, DatabaseProvisioner):
        raise ImproperlyConfigured(
            f'"{provisioner_path}" must subclass '
            f'multiverse.db.backends.base.DatabaseProvisioner.'
        )

    return provisioner_class

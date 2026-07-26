"""
Maps tenant databases onto dedicated Django connection aliases.

Why this exists
---------------
Earlier releases activated a tenant by rewriting
``settings.DATABASES['tenant']['NAME']`` in place. ``settings`` is a
process-global object, so two threads serving two tenants raced: the loser
executed its queries against the winner's database. That is the one failure a
multi-tenancy library must never have.

The fix removes the shared mutable state rather than guarding it. Each tenant
database gets its own alias — ``tenant::acme_db`` — registered once and never
mutated afterwards. Django already keeps ``ConnectionHandler._connections`` in a
thread-local, so once two threads use two different aliases they cannot see each
other's connections at all. Registration is idempotent and append-only, so there
is nothing left to race on.

The ``tenant`` entry in ``DATABASES`` stops being a live connection target and
becomes a *template*: it supplies the engine, credentials, host and options that
every derived alias inherits.
"""

from __future__ import annotations

import copy
import threading

from django.core.exceptions import ImproperlyConfigured
from django.db import connections

from multiverse.conf import multiverse_settings

#: Separates the template alias from the database name it was derived from.
#: Chosen because Django alias names are free-form strings but ``::`` never
#: appears in a hand-written alias, so a derived alias is recognisable on sight
#: in tracebacks and query logs.
ALIAS_SEPARATOR = '::'


class TenantConnectionRegistry:
    """Derives, registers and closes per-tenant connection aliases."""

    def __init__(self) -> None:
        self._registration_lock = threading.Lock()

    @property
    def base_alias(self) -> str:
        """Alias in ``DATABASES`` used as the template for tenant connections."""
        return multiverse_settings.tenant_database_alias

    def is_tenant_alias(self, alias: str | None) -> bool:
        """Whether ``alias`` addresses a tenant database (template or derived)."""
        if not alias:
            return False

        base = self.base_alias
        return alias == base or alias.startswith(base + ALIAS_SEPARATOR)

    def alias_for(self, database_name: str | None) -> str:
        """
        Return the connection alias serving ``database_name``.

        Falls back to the template alias when no database is given, and while
        running under a test suite. Tests deliberately stay on the template
        alias so that every query lands in the database Django's test runner
        created and rolls back — deriving new aliases there would reach real,
        un-sandboxed databases.
        """
        base_alias = self.base_alias

        if not database_name or multiverse_settings.testing:
            return base_alias

        alias = self._derive_alias(database_name)
        self._register(alias, database_name)
        return alias

    def close(self, database_name: str) -> None:
        """
        Close this process's connection to ``database_name``, if it has one.

        Django can only reach connections owned by the calling thread, so this
        is a best-effort cleanup for single-threaded callers such as management
        commands. Backends that can evict other processes' connections do so in
        their provisioner instead.
        """
        alias = self._derive_alias(database_name)

        for connection in connections.all(initialized_only=True):
            if connection.alias == alias:
                connection.close()

    def unregister(self, database_name: str) -> None:
        """
        Forget an alias, so a destroyed tenant cannot be reconnected to.

        Without this, an alias would outlive the database it names and the next
        activation of a recycled name would silently reuse stale settings.
        """
        alias = self._derive_alias(database_name)
        self.close(database_name)

        with self._registration_lock:
            connections.settings.pop(alias, None)

    def _derive_alias(self, database_name: str) -> str:
        return f'{self.base_alias}{ALIAS_SEPARATOR}{database_name}'

    def _register(self, alias: str, database_name: str) -> None:
        # Membership is re-checked on every call rather than memoised, because
        # this registry does not own `connections.settings`. `unregister()`
        # removes entries from it, and tests and downstream libraries replace it
        # wholesale. Re-checking costs one dict lookup and keeps the registry
        # self-healing instead of leaving dangling aliases behind.
        #
        # Note that `override_settings(DATABASES=...)` does *not* reach here:
        # `ConnectionHandler.settings` is a cached_property with no invalidating
        # receiver, which is why Django warns that overriding DATABASES leads to
        # unexpected behaviour. Patch `connections.settings` directly instead.
        if alias in connections.settings:
            return

        with self._registration_lock:
            if alias in connections.settings:
                return

            connections.settings[alias] = self._build_settings(database_name)

    def _build_settings(self, database_name: str) -> dict:
        """
        Clone the template alias and point the clone at ``database_name``.

        The template is read from ``connections.settings`` rather than from
        ``settings.DATABASES`` because Django has already filled in every
        default there (``OPTIONS``, ``CONN_MAX_AGE``, ``TEST`` and friends).
        Copying a raw ``DATABASES`` entry instead would produce an alias that
        blows up with ``KeyError`` on first use.
        """
        base_alias = self.base_alias

        try:
            template = connections.settings[base_alias]
        except KeyError as exc:
            raise ImproperlyConfigured(
                f"django-multiverse needs a '{base_alias}' entry in "
                f"settings.DATABASES to use as the template for tenant "
                f"connections. Add one with the engine and credentials your "
                f"tenant databases use, or point TENANT_DATABASE_ALIAS at an "
                f"existing entry."
            ) from exc

        entry = copy.deepcopy(template)
        entry['NAME'] = self._connection_name(base_alias, database_name)
        return entry

    @staticmethod
    def _connection_name(base_alias: str, database_name: str) -> str:
        """
        Ask the backend how it addresses ``database_name``.

        For most engines this is the name itself. For file-backed engines it is
        a resolved absolute path — and asking the backend is what guarantees
        that the file the provisioner creates is the same file Django then
        connects to. Deriving the two independently is how you end up with a
        freshly created database that nothing ever opens.
        """
        from multiverse.db.backends.base import get_provisioner

        provisioner = get_provisioner(base_alias)

        if provisioner is None:
            return database_name

        return provisioner.connection_name(database_name)


#: Process-wide registry. Stateless apart from a lock, so sharing one is safe.
tenant_connections = TenantConnectionRegistry()

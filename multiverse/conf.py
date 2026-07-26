"""
Engine-neutral settings.

Three rules govern this module:

1. **Every engine-neutral setting the package reads is declared here.**
   Scattering ``getattr(settings, ...)`` calls across modules makes it
   impossible to answer "what can I configure?" without reading the whole
   codebase.

2. **Nothing engine-specific belongs here.** A setting that only one backend
   consumes lives in that backend's module — ``TENANT_DATABASE_DIRECTORY`` with
   the SQLite provisioner, ``TENANT_PROVISIONING_DATABASE`` with the PostgreSQL
   one. Adding a backend must never require editing this file, or the
   provisioner abstraction is not buying anything.

3. **Nothing is cached.** Values are resolved on every access. Django's
   ``override_settings`` mutates the settings object in place, so a cached
   accessor would silently keep serving the pre-override value and quietly break
   every downstream test suite.
"""

from __future__ import annotations

from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured

DEFAULT_TENANT_DATABASE_ALIAS = 'tenant'
DEFAULT_TENANT_HEADER_NAME = 'X-Tenant'

#: Hostnames that resolve to the development tenant while ``DEBUG`` is on.
LOOPBACK_HOSTNAMES = frozenset({'127.0.0.1', '::1', 'localhost'})


class MultiverseSettings:
    """Typed, documented accessors for the settings this package understands."""

    @property
    def tenant_model(self) -> str:
        """Dotted ``app_label.ModelName`` of the tenant model. Required."""
        try:
            return django_settings.TENANT_MODEL
        except AttributeError as exc:
            raise ImproperlyConfigured(
                "TENANT_MODEL is not set. Add it to your settings, for example:\n"
                "    TENANT_MODEL = 'multiverse.Tenant'"
            ) from exc

    @property
    def tenant_database_alias(self) -> str:
        """
        Alias in ``DATABASES`` used as the template for tenant connections.

        This entry supplies the engine and credentials. Its ``NAME`` is only a
        placeholder: the real database name comes from the active tenant.
        """
        return getattr(
            django_settings,
            'TENANT_DATABASE_ALIAS',
            DEFAULT_TENANT_DATABASE_ALIAS,
        )

    @property
    def tenant_database_name(self) -> str | None:
        """
        Database used by the tenant alias when no tenant is active.

        Falls back to the ``NAME`` configured on the tenant alias, which is read
        from ``settings.DATABASES`` rather than from a live connection so that an
        active tenant can never contaminate the answer.
        """
        configured = getattr(django_settings, 'TENANT_DATABASE_NAME', None)
        if configured:
            return configured

        alias = self.tenant_database_alias
        return django_settings.DATABASES.get(alias, {}).get('NAME') or None

    @property
    def system_apps(self) -> list[str]:
        """Apps whose tables live only in the ``default`` database."""
        return list(getattr(django_settings, 'SYSTEM_APPS', []))

    @property
    def common_apps(self) -> list[str]:
        """Apps whose tables are created everywhere but always read from ``default``."""
        return list(getattr(django_settings, 'COMMON_APPS', []))

    @property
    def tenant_apps(self) -> list[str]:
        """Apps whose tables live only in tenant databases."""
        return list(getattr(django_settings, 'TENANT_APPS', []))

    @property
    def system_routes(self) -> list[str]:
        """
        URL namespaces served without a tenant (health checks, billing, sign-up).

        Matched against ``ResolverMatch.namespace`` and ``ResolverMatch.app_name``.
        """
        return list(getattr(django_settings, 'SYSTEM_ROUTES', []))

    @property
    def tenant_header_enabled(self) -> bool:
        """
        Whether the tenant may be selected by an inbound HTTP header.

        Defaults to ``DEBUG``: on in development, off in production.

        The header exists because subdomain routing does not work on
        ``localhost`` — there is no ``acme.localhost`` to send a browser to, and
        the loopback fallback below can only ever reach one tenant. Sending
        ``X-Tenant: acme`` is how you switch tenants locally.

        In production the same header is a liability: it overrides the hostname
        and any client can set it, so an unauthenticated request could name the
        tenant it wanted to be served. Turning it on there is a deliberate
        decision that requires a proxy which strips the inbound value and sets
        it itself, so it must be opted into explicitly.

        Set it to ``True`` or ``False`` to override the ``DEBUG`` default in
        either direction — ``False`` in development is how you reproduce
        production resolution locally.
        """
        configured = getattr(django_settings, 'TENANT_HEADER_ENABLED', None)

        if configured is None:
            return bool(django_settings.DEBUG)

        return bool(configured)

    @property
    def tenant_header_name(self) -> str:
        """Header consulted when :attr:`tenant_header_enabled` is on."""
        return getattr(
            django_settings,
            'TENANT_HEADER_NAME',
            DEFAULT_TENANT_HEADER_NAME,
        )

    @property
    def testing(self) -> bool:
        """
        Whether the package is running inside a test suite.

        This does **not** disable routing. It only stops the connection registry
        from deriving new aliases, which keeps every query inside the databases
        Django's test runner created and rolls back.
        """
        return bool(getattr(django_settings, 'TESTING', False))


multiverse_settings = MultiverseSettings()

"""
Resolving tenants, and provisioning the databases behind them.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.apps import apps as django_apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import models
from django.db.models import Q, QuerySet
from django.shortcuts import get_object_or_404

from multiverse.conf import LOOPBACK_HOSTNAMES, multiverse_settings
from multiverse.db.backends.base import DatabaseProvisioner, get_provisioner
from multiverse.db.backends.utils import (  # noqa: F401  (re-exported for backwards compatibility)
    get_tenant_database_alias,
    get_tenant_database_name,
)
from multiverse.db.connections import tenant_connections


def get_tenant_model() -> type[models.Model]:
    """Return the tenant model this project is configured to use."""
    tenant_model = multiverse_settings.tenant_model

    try:
        return django_apps.get_model(tenant_model, require_ready=False)
    except ValueError as exc:
        raise ImproperlyConfigured(
            "TENANT_MODEL must be of the form 'app_label.ModelName', "
            f'got {tenant_model!r}.'
        ) from exc
    except LookupError as exc:
        raise ImproperlyConfigured(
            f"TENANT_MODEL refers to model '{tenant_model}', which is not "
            f'installed. Add its app to INSTALLED_APPS.'
        ) from exc


def get_tenant_queryset(include_deleted: bool = False) -> QuerySet:
    """
    Base queryset for tenant lookups.

    Soft-deleted tenants are excluded by default. A decommissioned tenant must
    stop serving traffic the moment it is deleted, so every request-path lookup
    goes through here rather than through a bare ``.objects``.
    """
    tenant_model = get_tenant_model()

    if include_deleted:
        # `objects_with_deleted` comes from django-timestampable. A swapped
        # tenant model is not obliged to provide it, so fall back rather than
        # crash — the caller still gets a usable queryset, just without the
        # deleted rows.
        manager = getattr(
            tenant_model, 'objects_with_deleted', tenant_model._default_manager
        )
    else:
        manager = tenant_model._default_manager

    return manager.all()


def get_tenant(lookup: str | uuid.UUID | int, include_deleted: bool = False) -> Any:
    """
    Find a tenant by primary key, subdomain or database name.

    Raises the tenant model's ``DoesNotExist`` when nothing matches.
    """
    tenant_model = get_tenant_model()
    queryset = get_tenant_queryset(include_deleted=include_deleted)
    primary_key_field = tenant_model._meta.pk

    if matches_int_field(primary_key_field, lookup) or matches_uuid_field(
        primary_key_field, lookup
    ):
        return queryset.get(pk=lookup)

    return queryset.get(Q(subdomain=lookup) | Q(database_name=lookup))


def guess_tenant_from_request(request) -> Any:
    """
    Determine which tenant a request is addressed to.

    Resolution order:

    1. The tenant header, but only when ``TENANT_HEADER_ENABLED`` is on. The
       header is attacker-controlled and outranks the hostname, so it stays off
       unless a trusted proxy is known to be setting it.
    2. While ``DEBUG`` is on and the host is a loopback address, the tenant whose
       database matches ``TENANT_DATABASE_NAME`` — the local development tenant.
    3. Otherwise the first label of the hostname: ``acme.example.com`` → ``acme``.

    Raises ``Http404`` when no tenant matches, which Django renders as a normal
    404 rather than leaking that the hostname was understood but unknown.
    """
    queryset = get_tenant_queryset()
    subdomain = get_tenant_subdomain_from_request(request)

    if subdomain:
        return get_object_or_404(queryset, subdomain=subdomain)

    hostname = get_hostname_from_request(request)

    if settings.DEBUG and hostname in LOOPBACK_HOSTNAMES:
        return get_object_or_404(
            queryset, database_name=multiverse_settings.tenant_database_name
        )

    return get_object_or_404(queryset, subdomain=hostname.split('.')[0])


def is_tenant_subdomain_in_request(request, header_name: str | None = None) -> bool:
    """Whether the request carries a usable tenant header."""
    if not multiverse_settings.tenant_header_enabled:
        return False

    return (header_name or multiverse_settings.tenant_header_name) in request.headers


def get_tenant_subdomain_from_request(
    request, header_name: str | None = None
) -> str | None:
    """The subdomain named by the tenant header, if that header is trusted."""
    if not is_tenant_subdomain_in_request(request, header_name):
        return None

    header = header_name or multiverse_settings.tenant_header_name
    return request.headers[header].strip() or None


def get_hostname_from_request(request) -> str:
    """The request's hostname, lowercased and without the port."""
    return request.get_host().split(':')[0].lower()


def guess_tenant_database_name() -> str | None:
    """
    The database the tenant alias uses when no tenant is active.

    Read from ``settings.DATABASES``, never from a live connection. Earlier
    releases derived this from the connection's current name, which meant that
    after serving one tenant it reported *that tenant's* database as the
    baseline — so deactivating a tenant left the thread pointed at it.
    """
    return multiverse_settings.tenant_database_name


def get_common_apps(or_default: list[str] | None = None) -> list[str]:
    return multiverse_settings.common_apps or list(or_default or [])


def get_system_apps(or_default: list[str] | None = None) -> list[str]:
    return multiverse_settings.system_apps or list(or_default or [])


def get_tenant_apps(or_default: list[str] | None = None) -> list[str]:
    return multiverse_settings.tenant_apps or list(or_default or [])


def get_tenant_provisioner() -> DatabaseProvisioner | None:
    """
    The provisioner for the configured tenant engine, or ``None`` if it has no
    provisioning support.
    """
    return get_provisioner(tenant_connections.base_alias)


def create_tenant_database(tenant) -> tuple[str, bool]:
    """Create ``tenant``'s database if it does not already exist."""
    provisioner = get_tenant_provisioner()

    if provisioner is None:
        return tenant.database_name, False

    return provisioner.create_if_not_exists(tenant.database_name)


def drop_tenant_database(tenant) -> tuple[str, bool]:
    """
    Drop ``tenant``'s database if it exists.

    Local connections are closed and the alias forgotten first, so that the drop
    is not blocked by this process's own open handle and no later activation can
    reconnect to a database that is gone.
    """
    provisioner = get_tenant_provisioner()
    tenant_connections.unregister(tenant.database_name)

    if provisioner is None:
        return tenant.database_name, False

    return provisioner.drop_if_exists(tenant.database_name)


def migrate_tenant_database(tenant=None, *args, **options) -> None:
    """
    Run migrations against one tenant's database.

    Positional arguments are forwarded to ``migrate``, so a single app or a
    single migration can be targeted::

        migrate_tenant_database(tenant, 'invoices', '0007_add_currency')

    Pass the tenant explicitly whenever you know it. Earlier releases always
    migrated whichever database the shared tenant alias happened to point at, so
    ``create_tenant`` created a database and then migrated a different one,
    leaving every new tenant empty.
    """
    if tenant is not None:
        database = tenant_connections.alias_for(tenant.database_name)
    else:
        from multiverse.awareness import get_current_database_alias

        database = get_current_database_alias()

    call_command('migrate', *args, database=database, **options)


def is_valid_uuid(value: uuid.UUID | str) -> bool:
    """Whether ``value`` is a UUID, in any of its accepted textual forms."""
    if isinstance(value, uuid.UUID):
        return True

    if not isinstance(value, str):
        return False

    try:
        # Accepts hyphenated, unhyphenated, braced and urn: forms, in any case.
        # The previous implementation compared against `str(UUID(value))`, which
        # rejected every form but lowercase-hyphenated and sent valid keys down
        # the subdomain branch to fail as DoesNotExist.
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False

    return True


def matches_int_field(field: models.Field, value: Any) -> bool:
    return isinstance(field, (models.AutoField, models.IntegerField)) and isinstance(
        value, int
    )


def matches_uuid_field(field: models.Field, value: Any) -> bool:
    return (
        isinstance(field, models.UUIDField)
        and isinstance(value, (str, uuid.UUID))
        and is_valid_uuid(value)
    )

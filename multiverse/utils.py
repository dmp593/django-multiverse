import uuid

from django.db import models

from importlib import import_module
from types import ModuleType

from django.apps import apps as django_apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db.models import Q
from django.shortcuts import get_object_or_404


from multiverse.db.backends.utils import get_tenant_database_alias, get_tenant_database_name


def is_valid_uuid(val: uuid.UUID | str) -> bool:
    if isinstance(val, uuid.UUID):
        return True

    if not isinstance(val, str):
        return False

    try:
        uuid_obj = uuid.UUID(val)
        return str(uuid_obj) == val
    except ValueError:
        return False


def get_tenant_model():
    """
    Return the User model that is active in this project.
    """
    try:
        return django_apps.get_model(settings.TENANT_MODEL, require_ready=False)
    except ValueError:
        raise ImproperlyConfigured(
            "TENANT_MODEL must be of the form 'app_label.model_name'"
        )
    except LookupError:
        raise ImproperlyConfigured(
            "TENANT_MODEL refers to model '%s' that has not been installed"
            % settings.TENANT_MODEL
        )


def guess_tenant_database_name():
    tenant_database_alias = get_tenant_database_alias()
    tenant_database_settings = settings.DATABASES.get(tenant_database_alias, {})

    # if not found, the fallback will be the last set at settings.DATABASES[<tenant>]['NAME']
    # or :memory: in case even the name doesn't exist. (assuming sqlite ENGINE in this case)
    or_default = tenant_database_settings.get('NAME') or ':memory:'

    return get_tenant_database_name(or_default=or_default)


def guess_tenant_from_request(request):
    """
    Determines the tenant based on the incoming request.

    This function attempts to identify the tenant by examining the subdomain or hostname
    in the request. It follows these steps:

    1. Checks if the tenant subdomain (Header: X-Tenant) is present in the request.
    2. If not, it checks the hostname (Header: Host).
    3. If the application is running in DEBUG mode and the hostname is '127.0.0.1' or 'localhost',
       it returns the default tenant for the development environment.
    4. If none of the above conditions are met, it extracts the subdomain from the hostname.

    Args:
        request: The HTTP request object.

    Returns:
        Tenant: The tenant object corresponding to the subdomain or hostname.

    Raises:
        Http404: If no tenant is found for the given subdomain or hostname.

    """
    Tenant = get_tenant_model()

    if is_tenant_subdomain_in_request(request):
        subdomain = get_tenant_subdomain_from_request(request)
        return get_object_or_404(Tenant, subdomain=subdomain)

    hostname = get_hostname_from_request(request)

    if settings.DEBUG and hostname in ['127.0.0.1', 'localhost']:
        # Failed to guess tenant from request by neither headers: X-Tenant and Host.
        # If the app is running in debug mode, and it's a local IP or hostname,
        # allow getting the default tenant for the development environment.
        default_database_name = guess_tenant_database_name()
        return get_object_or_404(Tenant, database_name=default_database_name)

    subdomain = hostname.split('.')[0]
    return get_object_or_404(Tenant, subdomain=subdomain)


def is_tenant_subdomain_in_request(request, header_name: str = 'X-Tenant'):
    return header_name in request.headers


def get_tenant_subdomain_from_request(request, header_name: str = 'X-Tenant'):
    # split on `:` to remove the port
    if is_tenant_subdomain_in_request(request, header_name):
        return request.headers[header_name]

    return None


def get_hostname_from_request(request):
    # split on `:` to remove the port
    return request.get_host().split(':')[0].lower()


def matches_int_field(field: models.Field, value):
    return isinstance(field, models.AutoField) and isinstance(value, int)


def matches_uuid_field(field: models.Field, value):
    return isinstance(field, models.UUIDField) and isinstance(value, (str, uuid.UUID)) and is_valid_uuid(value)


def get_tenant(lookup: str | uuid.UUID | int):
    Tenant = get_tenant_model()

    pk_field = Tenant._meta.pk

    if matches_int_field(pk_field, lookup) or matches_uuid_field(pk_field, lookup):
        return Tenant.objects.get(pk=lookup)

    where = Q(
        _connector=Q.OR,
        subdomain=lookup,
        database_name=lookup
    )

    return Tenant.objects.get(where)


def get_common_apps(or_default: list[str] | None = None):
    return getattr(settings, 'COMMON_APPS',  or_default or [])


def get_system_apps(or_default: list[str] | None = None):
    return getattr(settings, 'SYSTEM_APPS',  or_default or [])


def get_tenant_apps(or_default: list[str] | None = None):
    return getattr(settings, 'TENANT_APPS',  or_default or [])


def get_db_utils_module(db_alias: str) -> ModuleType | None:
    if db_alias not in settings.DATABASES:
        return None

    db_engine = settings.DATABASES[db_alias]['ENGINE']

    if 'sqlite3' in db_engine:
        return import_module('tenants.db.backends.sqlite3.utils')

    if 'postgresql' in db_engine:
        return import_module('tenants.db.backends.postgresql.utils')

    return None


def create_tenant_database(tenant):
    db_alias = get_tenant_database_alias()
    db_utils_module = get_db_utils_module(db_alias)

    if db_utils_module and hasattr(db_utils_module, 'create_database_if_not_exists'):
        db_utils_module.create_database_if_not_exists(tenant.database_name)


def drop_tenant_database(tenant):
    db_alias = get_tenant_database_alias()
    db_utils_module = get_db_utils_module(db_alias)

    if db_utils_module and hasattr(db_utils_module, 'drop_database_if_exists'):
        db_utils_module.drop_database_if_exists(tenant.database_name)


def migrate_tenant_database():
    # Run migrations for the new tenant's database
    call_command('migrate', database=get_tenant_database_alias())

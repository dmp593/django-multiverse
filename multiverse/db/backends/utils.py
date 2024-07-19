from functools import cache
from django.conf import settings


@cache
def get_tenant_database_alias(or_default: str = 'tenant'):
    return getattr(settings, 'TENANT_DATABASE_ALIAS', or_default)


@cache
def get_tenant_database_name(or_default: str = ':memory:'):
    return getattr(settings, 'TENANT_DATABASE_NAME', or_default)

import threading

from typing import Any

from django.conf import settings
from django.db import connections

from multiverse.db.backends.utils import get_tenant_database_alias
from multiverse.signals import tenant_changed
from multiverse.utils import guess_tenant_database_name


__thread_local__ = threading.local()


def get_current_tenant():
    return getattr(__thread_local__, 'current_tenant', None)


def set_current_tenant(tenant: Any):
    setattr(__thread_local__, 'current_tenant', tenant)

    alias = get_tenant_database_alias()
    connections[alias].close()  # close any pending tenant connections...

    # We shouldn't alter settings at runtime...
    # https://docs.djangxoproject.com/en/5.0/topics/settings/#altering-settings-at-runtime
    settings.DATABASES[alias]['NAME'] = tenant.database_name

    tenant_changed.send(sender=tenant.__class__, instance=tenant)


def forget_current_tenant():
    setattr(__thread_local__, 'current_tenant', None)

    alias = get_tenant_database_alias()
    connections[alias].close()  # close any pending tenant connections...

    # We shouldn't alter settings at runtime...
    # https://docs.djangxoproject.com/en/5.0/topics/settings/#altering-settings-at-runtime
    settings.DATABASES[alias]['NAME'] = guess_tenant_database_name()


def get_request():
    return getattr(__thread_local__, 'request', None)


def set_request(request: Any = None):
    return getattr(__thread_local__, 'request', request)

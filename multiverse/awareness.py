"""
Tracks which tenant the current thread is serving.

All state here is thread-local, and it is *only* state — activating a tenant
records a fact and notifies listeners. It no longer touches ``settings``, opens
or closes connections, or has any other side effect. Choosing the database that
follows from that fact is :mod:`multiverse.db.connections`' job, and it happens
lazily, on demand, from :func:`get_current_database_alias`.

Keeping activation free of side effects is what makes it safe to nest, to use in
a ``finally`` block, and to reason about under concurrency.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from multiverse.db.connections import tenant_connections
from multiverse.signals import tenant_changed

_state = threading.local()


__all__ = [
    'get_current_tenant',
    'set_current_tenant',
    'forget_current_tenant',
    'tenant_context',
    'get_current_database_alias',
    'get_request',
    'set_request',
    'forget_request',
]


def get_current_tenant() -> Any | None:
    """The tenant this thread is serving, or ``None`` outside a tenant."""
    return getattr(_state, 'current_tenant', None)


def set_current_tenant(tenant: Any | None) -> None:
    """
    Make ``tenant`` current for this thread, or clear it when given ``None``.

    Prefer :func:`tenant_context`, which cannot leak the tenant into whatever
    the thread does next. Reach for this only when activation and deactivation
    genuinely cannot be bracketed by a ``with`` block.
    """
    if tenant is not None and not hasattr(tenant, 'database_name'):
        raise TypeError(
            f'set_current_tenant() expects a tenant instance or None, got '
            f'{type(tenant).__name__}. To activate a tenant by subdomain, '
            f'primary key or database name, resolve it first with '
            f'multiverse.utils.get_tenant().'
        )

    _state.current_tenant = tenant

    from multiverse.utils import get_tenant_model

    tenant_changed.send(sender=get_tenant_model(), instance=tenant)


def forget_current_tenant() -> None:
    """Clear the current tenant, returning this thread to the base database."""
    set_current_tenant(None)


@contextmanager
def tenant_context(tenant: Any | None) -> Iterator[Any | None]:
    """
    Serve ``tenant`` for the duration of the block, then restore what came before.

    This is the primary way to activate a tenant. Restoring the *previous*
    tenant rather than clearing outright means the blocks nest, so a job that
    fans out over several tenants can be written as a plain loop::

        for tenant in Tenant.objects.all():
            with tenant_context(tenant):
                rebuild_search_index()
    """
    previous = get_current_tenant()
    set_current_tenant(tenant)

    try:
        yield tenant
    finally:
        set_current_tenant(previous)


def get_current_database_alias() -> str:
    """
    Connection alias serving this thread's tenant.

    Derived on every call rather than cached at activation time. Django discards
    dynamically registered aliases whenever ``DATABASES`` is overridden, and a
    cached alias would survive as a dangling reference to a connection that no
    longer exists.
    """
    tenant = get_current_tenant()
    database_name = getattr(tenant, 'database_name', None)

    return tenant_connections.alias_for(database_name)


def get_request() -> Any | None:
    """The request being served by this thread, if the middleware stored one."""
    return getattr(_state, 'request', None)


def set_request(request: Any) -> None:
    """Record the request being served by this thread."""
    _state.request = request


def forget_request() -> None:
    """
    Drop the stored request.

    Requests hold references to sessions, uploaded files and database
    connections, so leaving one attached to a pooled worker thread keeps all of
    that alive until the thread happens to serve another request.
    """
    _state.request = None

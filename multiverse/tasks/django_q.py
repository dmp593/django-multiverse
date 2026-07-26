"""
Tenant-aware wrappers around django-q.

A queued task runs in a cluster worker that has no request and therefore no
tenant. These wrappers capture the tenant that was active at enqueue time, carry
its identifier through the broker, and re-activate it inside the worker.

Two rules keep that safe:

* the tenant is re-activated inside a ``tenant_context`` block, so it is released
  when the task finishes — a worker that keeps a tenant active runs the *next*,
  unrelated task against the previous customer's database;
* only the tenant's primary key travels through the broker. Serialising the
  tenant object would pin a stale copy of its row into every queued payload.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from functools import cache
from importlib import import_module
from typing import Any

from django_q.tasks import async_task as django_q_async_task
from django_q.tasks import schedule as django_q_schedule
from django_q.utils import get_func_repr

from multiverse.awareness import get_current_tenant, tenant_context
from multiverse.utils import get_tenant


def tenant_aware_func(
    fn: str,
    *,
    tenant_id: str | int | None = None,
    fn_args: tuple | None = None,
    fn_kwargs: dict | None = None,
) -> Any:
    """
    Worker-side entry point: activate the tenant, then call the real function.

    Not intended to be called directly — :func:`async_task` and :func:`schedule`
    enqueue it on your behalf.
    """
    tenant = get_tenant(tenant_id) if tenant_id is not None else None

    with tenant_context(tenant):
        return _import_callable(fn)(*(fn_args or ()), **(fn_kwargs or {}))


def async_task(func: Callable | str, *args, q_options: dict | None = None, **kwargs):
    """
    Queue ``func`` to run under the currently active tenant.

    ``args`` and ``kwargs`` are passed to ``func``. Options for django-q itself
    (``hook``, ``group``, ``timeout``, ``sync`` …) go in ``q_options``, which is
    django-q's own mechanism for telling the two apart — without it there is no
    way to distinguish a task argument named ``timeout`` from the queue option
    of the same name.
    """
    tenant = get_current_tenant()

    if tenant is None:
        return django_q_async_task(
            get_func_repr(func), *args, **(q_options or {}), **kwargs
        )

    return django_q_async_task(
        get_func_repr(tenant_aware_func),
        **(q_options or {}),
        fn=get_func_repr(func),
        tenant_id=str(tenant.pk),
        fn_args=args,
        fn_kwargs=kwargs,
    )


def schedule(func: Callable | str, *args, **kwargs):
    """
    Schedule ``func`` to run repeatedly under the currently active tenant.

    Scheduler options (``schedule_type``, ``next_run``, ``name``, ``repeats`` …)
    and the target function's own keyword arguments are separated automatically,
    so the call reads exactly as django-q's does::

        schedule('reports.rebuild', schedule_type=Schedule.DAILY, name='rebuild')
    """
    option_names = _schedule_option_names()
    options = {
        name: kwargs.pop(name) for name in list(kwargs) if name in option_names
    }

    tenant = get_current_tenant()

    if tenant is None:
        # Previously this branch called django-q's *async_task*, silently
        # turning every schedule created outside a tenant into a one-shot task
        # that ran once and never again.
        return django_q_schedule(get_func_repr(func), *args, **options, **kwargs)

    return django_q_schedule(
        get_func_repr(tenant_aware_func),
        **options,
        fn=get_func_repr(func),
        tenant_id=str(tenant.pk),
        fn_args=args,
        fn_kwargs=kwargs,
    )


@cache
def _schedule_option_names() -> frozenset[str]:
    """
    Keyword arguments that belong to django-q's scheduler rather than to the task.

    Read from django-q's own signature instead of being hardcoded: this package
    supports a range of django-q versions, and a list that drifted out of date
    would silently deliver a scheduler option to the task as a function argument.

    Caching is safe here in a way it is not for settings — a function signature
    cannot change while the process is running.
    """
    parameters = inspect.signature(django_q_schedule).parameters

    return frozenset(
        name
        for name, parameter in parameters.items()
        if parameter.kind
        in (parameter.KEYWORD_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ) - {'func'}


def _import_callable(dotted_path: str) -> Callable:
    module_path, attribute_name = dotted_path.rsplit('.', 1)

    return getattr(import_module(module_path), attribute_name)

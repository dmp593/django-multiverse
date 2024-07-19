from importlib import import_module

from django_q.tasks import schedule as django_q_schedule, async_task as django_q_async_task
from django_q.utils import get_func_repr

from multiverse.awareness import get_current_tenant, set_current_tenant
from multiverse.utils import get_tenant


def tenant_aware_func(tenant_id, func, *args, **kwargs):
    tenant = get_tenant(tenant_id)
    set_current_tenant(tenant)

    if isinstance(func, str):
        module_name, func_name = func.rsplit('.', 1)

        module = import_module(module_name)
        func = getattr(module, func_name)

    return func(*args, **kwargs)


def async_task(func, *args, **kwargs):
    tenant = get_current_tenant()

    if not tenant:
        return django_q_async_task(get_func_repr(func), *args, **kwargs)

    return django_q_async_task(
        get_func_repr(tenant_aware_func),
        str(tenant.pk),
        get_func_repr(func),
        *args,
        **kwargs
    )


def schedule(func, *args, **kwargs):
    tenant = get_current_tenant()

    if not tenant:
        return django_q_async_task(get_func_repr(func), *args, **kwargs)

    return django_q_schedule(
        get_func_repr(tenant_aware_func),
        str(tenant.pk),
        get_func_repr(func),
        *args,
        **kwargs
    )

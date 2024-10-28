from importlib import import_module
from django_q.tasks import schedule as django_q_schedule, async_task as django_q_async_task
from django_q.utils import get_func_repr
from multiverse.awareness import get_current_tenant, set_current_tenant
from multiverse.utils import get_tenant


def tenant_aware_func(fn: str, *, tenant_id: str | int = None, fn_args: tuple = None, fn_kwargs: dict = None):
    if tenant_id is not None:
        tenant = get_tenant(tenant_id)
        set_current_tenant(tenant)

    module_name, fn_name = fn.rsplit('.', 1)
    module = import_module(module_name)
    fn = getattr(module, fn_name)

    if fn_args is None:
        fn_args = ()

    if fn_kwargs is None:
        fn_kwargs = {}

    return fn(*fn_args, **fn_kwargs)


def async_task(func, *args, **kwargs):
    tenant = get_current_tenant()

    if not tenant:
        return django_q_async_task(get_func_repr(func), *args, **kwargs)

    return django_q_async_task(
        get_func_repr(tenant_aware_func),
        fn=get_func_repr(func),
        tenant_id=str(tenant.pk),
        fn_args=args,
        fn_kwargs=kwargs
    )


def schedule(func, *args, **kwargs):
    tenant = get_current_tenant()

    if not tenant:
        return django_q_schedule(get_func_repr(func), *args, **kwargs)

    return django_q_schedule(
        get_func_repr(tenant_aware_func),
        fn=get_func_repr(func),
        tenant_id=str(tenant.pk),
        fn_args=args,
        fn_kwargs=kwargs
    )

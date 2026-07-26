"""
Carrying a tenant through a background queue.

Skipped when django-q2 is not installed, since it is an optional extra.

django-q's own functions are patched rather than executed: these tests are about
what gets *enqueued* — which function, under which tenant, with which arguments
split which way — not about whether django-q can run it.
"""

from __future__ import annotations

import re
from unittest import mock

from django.test import SimpleTestCase, TestCase

try:
    import django_q  # noqa: F401

    DJANGO_Q_INSTALLED = True
except ImportError:  # pragma: no cover - exercised only without the extra
    DJANGO_Q_INSTALLED = False

if DJANGO_Q_INSTALLED:
    import inspect

    from django_q.tasks import schedule as django_q_schedule

    from multiverse.tasks.django_q import (
        SCHEDULE_OPTION_NAMES,
        async_task,
        schedule,
        tenant_aware_func,
    )

from unittest import skipUnless

from multiverse.awareness import get_current_tenant, tenant_context
from multiverse.models import Tenant

TARGET = 'tests.test_tasks.record_call'

#: Recorded by :func:`record_call`, so worker-side behaviour is observable.
CALLS: list[dict] = []


def record_call(*args, **kwargs):
    """Stand-in for a real task. Records the tenant it ran under."""
    tenant = get_current_tenant()

    CALLS.append({
        'args': args,
        'kwargs': kwargs,
        'tenant': tenant.subdomain if tenant else None,
    })

    return 'done'


@skipUnless(DJANGO_Q_INSTALLED, 'django-q2 is not installed')
class ScheduleOptionNamesTests(SimpleTestCase):
    def test_the_option_list_matches_django_q(self):
        """
        The guard on a hardcoded list.

        `schedule(func, *args, **kwargs)` declares no named parameters, so this
        cannot be introspected — an earlier attempt to derive it returned an
        empty set and silently delivered every scheduler option to the task as a
        function argument. Reading the `kwargs.pop(...)` calls out of django-q's
        source turns a version drift into a failing test rather than a task that
        runs once and never again.
        """
        source = inspect.getsource(django_q_schedule)
        popped = set(re.findall(r'kwargs\.pop\(\s*["\'](\w+)["\']', source))

        self.assertEqual(
            set(SCHEDULE_OPTION_NAMES),
            popped,
            'django-q changed which options schedule() consumes; update '
            'SCHEDULE_OPTION_NAMES to match',
        )

    def test_the_option_list_is_not_empty(self):
        """A regression guard: the derived version silently produced nothing."""
        self.assertTrue(SCHEDULE_OPTION_NAMES)


@skipUnless(DJANGO_Q_INSTALLED, 'django-q2 is not installed')
class ScheduleTests(TestCase):
    databases = '__all__'

    def setUp(self):
        self.tenant = Tenant.objects.create(subdomain='acme', database_name='db_acme')

    def _capture(self, *args, **kwargs):
        with mock.patch('multiverse.tasks.django_q.django_q_schedule') as patched:
            schedule(*args, **kwargs)

        return patched.call_args

    def test_scheduler_options_reach_django_q_not_the_task(self):
        """
        The bug this separation exists to prevent: `schedule_type` and `name`
        being delivered to the task as function keyword arguments, producing a
        schedule that fires once with arguments the task never expected.
        """
        with tenant_context(self.tenant):
            call = self._capture(TARGET, schedule_type='D', name='nightly')

        self.assertEqual(call.kwargs['schedule_type'], 'D')
        self.assertEqual(call.kwargs['name'], 'nightly')
        self.assertEqual(call.kwargs['fn_kwargs'], {})

    def test_task_arguments_reach_the_task_not_django_q(self):
        with tenant_context(self.tenant):
            call = self._capture(TARGET, 7, schedule_type='D', report='monthly')

        self.assertEqual(call.kwargs['fn_args'], (7,))
        self.assertEqual(call.kwargs['fn_kwargs'], {'report': 'monthly'})

    def test_the_tenant_is_carried_by_primary_key_only(self):
        """
        Serialising the tenant object would pin a stale copy of its row into
        every queued payload.
        """
        with tenant_context(self.tenant):
            call = self._capture(TARGET, schedule_type='D')

        self.assertEqual(call.kwargs['tenant_id'], str(self.tenant.pk))
        self.assertEqual(call.kwargs['fn'], TARGET)

    def test_without_a_tenant_a_schedule_is_still_a_schedule(self):
        """
        This branch called django-q's *async_task*, turning every schedule
        created outside a tenant into a one-shot task.
        """
        with mock.patch('multiverse.tasks.django_q.django_q_schedule') as patched:
            schedule(TARGET, schedule_type='D', name='nightly')

        patched.assert_called_once()
        self.assertEqual(patched.call_args.kwargs['schedule_type'], 'D')


@skipUnless(DJANGO_Q_INSTALLED, 'django-q2 is not installed')
class AsyncTaskTests(TestCase):
    databases = '__all__'

    def setUp(self):
        self.tenant = Tenant.objects.create(subdomain='acme', database_name='db_acme')

    def _capture(self, *args, **kwargs):
        with mock.patch('multiverse.tasks.django_q.django_q_async_task') as patched:
            async_task(*args, **kwargs)

        return patched.call_args

    def test_the_task_is_wrapped_when_a_tenant_is_active(self):
        with tenant_context(self.tenant):
            call = self._capture(TARGET, 7, report='monthly')

        self.assertEqual(call.kwargs['fn'], TARGET)
        self.assertEqual(call.kwargs['tenant_id'], str(self.tenant.pk))
        self.assertEqual(call.kwargs['fn_args'], (7,))
        self.assertEqual(call.kwargs['fn_kwargs'], {'report': 'monthly'})

    def test_queue_options_are_forwarded_rather_than_passed_to_the_task(self):
        """
        `q_options` is django-q's own mechanism for the ambiguity: without it
        there is no way to tell a task argument named `timeout` from the queue
        option of the same name.
        """
        with tenant_context(self.tenant):
            call = self._capture(TARGET, q_options={'group': 'reports'})

        self.assertEqual(call.kwargs['group'], 'reports')
        self.assertEqual(call.kwargs['fn_kwargs'], {})

    def test_without_a_tenant_the_task_is_enqueued_directly(self):
        call = self._capture(TARGET, 7)

        self.assertEqual(call.args, (TARGET, 7))
        self.assertNotIn('tenant_id', call.kwargs)


@skipUnless(DJANGO_Q_INSTALLED, 'django-q2 is not installed')
class WorkerSideTests(TestCase):
    databases = '__all__'

    def setUp(self):
        CALLS.clear()
        self.tenant = Tenant.objects.create(subdomain='acme', database_name='db_acme')

    def test_the_tenant_is_active_while_the_task_runs(self):
        tenant_aware_func(
            TARGET, tenant_id=str(self.tenant.pk), fn_args=(1,), fn_kwargs={'a': 2}
        )

        self.assertEqual(CALLS[0]['tenant'], 'acme')
        self.assertEqual(CALLS[0]['args'], (1,))
        self.assertEqual(CALLS[0]['kwargs'], {'a': 2})

    def test_the_tenant_is_released_when_the_task_finishes(self):
        """
        A worker that keeps a tenant active runs the *next*, unrelated task
        against the previous customer's database.
        """
        tenant_aware_func(TARGET, tenant_id=str(self.tenant.pk))

        self.assertIsNone(get_current_tenant())

    def test_the_tenant_is_released_when_the_task_raises(self):
        with self.assertRaises(ZeroDivisionError):
            tenant_aware_func('tests.test_tasks.explode', tenant_id=str(self.tenant.pk))

        self.assertIsNone(get_current_tenant())

    def test_a_task_with_no_tenant_runs_without_one(self):
        tenant_aware_func(TARGET)

        self.assertIsNone(CALLS[0]['tenant'])
        self.assertIsNone(get_current_tenant())


def explode():
    """A task that fails, to prove the tenant is released on the error path."""
    return 1 / 0

"""
Isolation guarantees.

These are the tests that matter most: every one of them reproduces a way this
package previously served one tenant's data to another. They are deliberately
written against observable behaviour — which file a connection actually opens,
which database a query actually reaches — rather than against internals, so a
future refactor cannot make them pass vacuously.
"""

from __future__ import annotations

import threading

from django.db import connections
from django.test import SimpleTestCase, TestCase

from multiverse.awareness import (
    forget_current_tenant,
    get_current_database_alias,
    get_current_tenant,
    set_current_tenant,
    tenant_context,
)
from multiverse.db.connections import tenant_connections
from multiverse.models import Tenant
from tests.support import DerivesTenantAliases


class ConcurrentActivationTests(DerivesTenantAliases, SimpleTestCase):
    """
    The regression guard for the cross-tenant leak.

    Two threads activate two different tenants and then each asks which database
    it is pointed at. Before the connection registry existed, tenant activation
    wrote the database name into process-global ``settings``, so the second
    thread's write clobbered the first thread's and one of them silently read
    the other's data.
    """

    def test_threads_do_not_observe_each_others_tenant(self):
        started = threading.Barrier(2)
        observed: dict[str, str] = {}

        for name in ('alpha', 'beta'):
            self.forget_alias(f'db_{name}')

        def activate_and_observe(name: str) -> None:
            tenant = Tenant(subdomain=name, database_name=f'db_{name}')

            with tenant_context(tenant):
                # Both threads activate before either observes, so a shared
                # slot would have been overwritten by the time we look.
                started.wait(timeout=5)
                observed[name] = get_current_database_alias()

        threads = [
            threading.Thread(target=activate_and_observe, args=(name,))
            for name in ('alpha', 'beta')
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(len(observed), 2, 'a worker thread did not finish')
        self.assertNotEqual(
            observed['alpha'],
            observed['beta'],
            'both threads resolved to the same database alias: one tenant is '
            'reading another tenant\'s data',
        )
        self.assertIn('db_alpha', observed['alpha'])
        self.assertIn('db_beta', observed['beta'])

    def test_derived_aliases_point_at_distinct_databases(self):
        alpha = self.alias_for('db_distinct_alpha')
        beta = self.alias_for('db_distinct_beta')

        self.assertNotEqual(alpha, beta)
        self.assertNotEqual(
            connections.settings[alpha]['NAME'],
            connections.settings[beta]['NAME'],
        )

    def test_derived_alias_inherits_engine_from_the_template(self):
        alias = self.alias_for('db_inherits')
        template = connections.settings[tenant_connections.base_alias]

        self.assertEqual(
            connections.settings[alias]['ENGINE'], template['ENGINE']
        )
        # Django fills these in on the template; a hand-built entry that omitted
        # them would raise KeyError on first use rather than at registration.
        self.assertIn('OPTIONS', connections.settings[alias])
        self.assertIn('TEST', connections.settings[alias])


class ActivationLifecycleTests(DerivesTenantAliases, SimpleTestCase):
    def test_context_manager_releases_the_tenant(self):
        tenant = Tenant(subdomain='acme', database_name='db_acme')

        with tenant_context(tenant):
            self.assertEqual(get_current_tenant(), tenant)

        self.assertIsNone(get_current_tenant())

    def test_context_manager_releases_the_tenant_on_exception(self):
        tenant = Tenant(subdomain='acme', database_name='db_acme')

        with self.assertRaises(RuntimeError):
            with tenant_context(tenant):
                raise RuntimeError('view exploded')

        self.assertIsNone(get_current_tenant())

    def test_contexts_nest_and_restore_the_outer_tenant(self):
        outer = Tenant(subdomain='outer', database_name='db_outer')
        inner = Tenant(subdomain='inner', database_name='db_inner')

        with tenant_context(outer):
            with tenant_context(inner):
                self.assertEqual(get_current_tenant(), inner)

            self.assertEqual(get_current_tenant(), outer)

    def test_forgetting_returns_to_the_base_database_not_the_last_tenant(self):
        """
        ``forget_current_tenant`` used to resolve the base database by reading
        the name the previous activation had just written, so it "reset" to the
        tenant it was supposed to be forgetting.
        """
        base_alias = tenant_connections.base_alias
        self.forget_alias('db_acme')

        set_current_tenant(Tenant(subdomain='acme', database_name='db_acme'))
        self.assertNotEqual(get_current_database_alias(), base_alias)

        forget_current_tenant()
        self.assertEqual(get_current_database_alias(), base_alias)

    def test_activation_rejects_a_non_tenant(self):
        with self.assertRaises(TypeError):
            set_current_tenant('acme')

    def tearDown(self):
        forget_current_tenant()


class SignalTests(TestCase):
    databases = '__all__'

    def setUp(self):
        self.received = []

        from multiverse.signals import tenant_changed

        self.signal = tenant_changed
        self.signal.connect(self._record)
        self.addCleanup(self.signal.disconnect, self._record)

    def _record(self, sender, instance, **kwargs):
        self.received.append(instance)

    def test_signal_fires_on_activation_and_on_release(self):
        tenant = Tenant.objects.create(subdomain='acme', database_name='db_acme')

        with tenant_context(tenant):
            pass

        # Release is signalled too. Receivers that namespace a cache or set a
        # logging context need to know when the tenant goes away, and earlier
        # releases only ever told them when it arrived.
        self.assertEqual(self.received, [tenant, None])

    def test_signal_sender_is_the_tenant_model(self):
        senders = []

        def record_sender(sender, **kwargs):
            senders.append(sender)

        # weak=False: Django holds receivers weakly, so a local function with no
        # other reference is garbage-collected before the signal ever fires.
        self.signal.connect(record_sender, weak=False)
        self.addCleanup(self.signal.disconnect, record_sender)

        with tenant_context(None):
            pass

        self.assertEqual(senders[0], Tenant)


class RegistrySelfHealingTests(DerivesTenantAliases, SimpleTestCase):
    def test_alias_is_reregistered_after_databases_are_overridden(self):
        """
        Nothing owns the alias registry exclusively: destroying a tenant
        unregisters its alias, and tests patch the connection settings.
        Deriving the alias on demand rather than caching it at activation time
        is what makes an externally removed alias survivable.
        """
        alias = self.alias_for('db_healing')
        self.assertIn(alias, connections.settings)

        connections.settings.pop(alias)

        self.assertEqual(tenant_connections.alias_for('db_healing'), alias)
        self.assertIn(alias, connections.settings)

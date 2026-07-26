"""Request-to-tenant binding, and the ways it used to go wrong."""

from __future__ import annotations

from django.test import Client, TestCase, override_settings

from multiverse.awareness import get_current_tenant, set_current_tenant
from multiverse.models import Tenant


class TenantResolutionTests(TestCase):
    databases = '__all__'

    def setUp(self):
        self.tenant = Tenant.objects.create(subdomain='acme', database_name='db_acme')
        self.client = Client()

    def test_the_tenant_is_resolved_from_the_hostname(self):
        response = self.client.get('/whoami/', HTTP_HOST='acme.example.com')

        self.assertEqual(response.json()['tenant'], 'acme')

    def test_an_unknown_subdomain_is_a_404(self):
        response = self.client.get('/whoami/', HTTP_HOST='nobody.example.com')

        self.assertEqual(response.status_code, 404)

    def test_a_soft_deleted_tenant_no_longer_resolves(self):
        """A decommissioned tenant must stop serving traffic immediately."""
        self.tenant.delete()

        response = self.client.get('/whoami/', HTTP_HOST='acme.example.com')

        self.assertEqual(response.status_code, 404)

    def test_the_tenant_is_released_after_the_request(self):
        self.client.get('/whoami/', HTTP_HOST='acme.example.com')

        self.assertIsNone(get_current_tenant())


class TenantHeaderTests(TestCase):
    databases = '__all__'

    def setUp(self):
        Tenant.objects.create(subdomain='acme', database_name='db_acme')
        Tenant.objects.create(subdomain='victim', database_name='db_victim')
        self.client = Client()

    def test_the_header_is_ignored_by_default(self):
        """
        The header outranks the hostname, and any client can set it. Trusting it
        unconditionally let an unauthenticated request choose which customer's
        database to read.
        """
        response = self.client.get(
            '/whoami/', HTTP_HOST='acme.example.com', HTTP_X_TENANT='victim'
        )

        self.assertEqual(response.json()['tenant'], 'acme')

    @override_settings(TENANT_HEADER_ENABLED=True)
    def test_the_header_is_honoured_when_explicitly_enabled(self):
        response = self.client.get(
            '/whoami/', HTTP_HOST='acme.example.com', HTTP_X_TENANT='victim'
        )

        self.assertEqual(response.json()['tenant'], 'victim')

    @override_settings(TENANT_HEADER_ENABLED=True, TENANT_HEADER_NAME='X-Customer')
    def test_the_header_name_is_configurable(self):
        response = self.client.get(
            '/whoami/', HTTP_HOST='acme.example.com', HTTP_X_CUSTOMER='victim'
        )

        self.assertEqual(response.json()['tenant'], 'victim')


@override_settings(SYSTEM_ROUTES=['health'])
class SystemRouteTests(TestCase):
    databases = '__all__'

    def setUp(self):
        Tenant.objects.create(subdomain='acme', database_name='db_acme')
        self.client = Client()

    def test_a_system_route_is_served_without_a_tenant(self):
        response = self.client.get('/health/', HTTP_HOST='anything.example.com')

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['tenant'])

    def test_a_system_route_clears_a_tenant_left_over_on_the_thread(self):
        """
        Skipping activation is not the same as deactivating. A worker thread
        that had just served a customer would run the next health check against
        that customer's database.
        """
        set_current_tenant(Tenant(subdomain='stale', database_name='db_stale'))
        self.addCleanup(set_current_tenant, None)

        response = self.client.get('/health/', HTTP_HOST='anything.example.com')

        self.assertIsNone(response.json()['tenant'])

    def test_an_unmatched_url_is_a_404_and_not_a_500(self):
        """
        The middleware resolved every URL to check it against SYSTEM_ROUTES.
        An unmatched path raised Resolver404 out of middleware, where Django
        cannot turn it into a 404 — so every scan for /wp-admin/ became a 500.
        """
        response = self.client.get('/no-such-url/', HTTP_HOST='acme.example.com')

        self.assertEqual(response.status_code, 404)

"""
DRF test helpers.

Skipped when djangorestframework is not installed, since it is an optional
extra. These are thin subclasses, so the risk is not logic but wiring: the MRO
deciding whether the tenant or DRF's client wins.
"""

from __future__ import annotations

from unittest import skipUnless

try:
    import rest_framework  # noqa: F401

    DRF_INSTALLED = True
except ImportError:  # pragma: no cover - exercised only without the extra
    DRF_INSTALLED = False

if DRF_INSTALLED:
    from multiverse.test.drf import TenantAPIClient, TenantAPITestCase


@skipUnless(DRF_INSTALLED, 'djangorestframework is not installed')
class TenantAPITestCaseTests(TenantAPITestCase if DRF_INSTALLED else object):
    tenant_subdomain = 'acme'

    def test_the_tenant_is_created_and_active(self):
        self.assertEqual(self.tenant.subdomain, 'acme')

    def test_the_client_is_a_tenant_aware_api_client(self):
        """
        `TenantAPIClient(TenantClient, APIClient)` — the tenant mixin must win
        the MRO, or requests go out without a tenant host and DRF's client
        silently takes over.
        """
        self.assertIsInstance(self.client, TenantAPIClient)
        self.assertEqual(self.client.tenant, self.tenant)

    def test_requests_are_addressed_to_the_tenant(self):
        response = self.client.get('/whoami/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['tenant'], 'acme')

    def test_drf_client_features_survive(self):
        """The point of the DRF variant is `format=`, `force_authenticate`, etc."""
        self.assertTrue(hasattr(self.client, 'force_authenticate'))

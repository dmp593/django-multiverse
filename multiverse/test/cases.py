"""
Test cases that run inside a tenant.

Subclass :class:`TenantTestCase` and a tenant is created, activated for each
test, and released afterwards. Routing stays fully active, so a test exercises
the same router decisions production does.
"""

from __future__ import annotations

from django.test import TestCase, modify_settings, override_settings

from multiverse.awareness import forget_current_tenant, set_current_tenant
from multiverse.conf import multiverse_settings
from multiverse.test.client import TenantClient
from multiverse.utils import get_tenant_model


class TenantTestCaseMixin:
    """
    Mixin that provides a tenant to any Django test case class.

    Configure the tenant declaratively::

        class InvoiceTests(TenantTestCase):
            tenant_subdomain = 'acme'
    """

    #: Populated during ``setUpClass`` with the tenant these tests run as.
    tenant = None

    #: Subdomain of the tenant to create. Also added to ``ALLOWED_HOSTS``.
    tenant_subdomain = 'test'

    #: Database backing the tenant. Defaults to the test tenant database, so
    #: queries land in the database Django's test runner created and rolls back.
    tenant_database_name = None

    #: Tenant data lives outside `default`, so tests need every alias declared.
    databases = '__all__'

    @classmethod
    def setUpClass(cls):
        # Applied through Django's own override helpers rather than by mutating
        # settings directly: they restore the previous value on exit and emit
        # `setting_changed`, which is what invalidates the router's cached app
        # classification. Poking at settings by hand did neither, so state
        # leaked between test classes.
        cls._multiverse_overrides = [
            override_settings(TESTING=True),
            modify_settings(ALLOWED_HOSTS={'append': cls.tenant_subdomain}),
        ]

        for override in cls._multiverse_overrides:
            override.enable()

        try:
            super().setUpClass()
            cls.tenant = cls._create_tenant()
        except Exception:
            cls._disable_multiverse_overrides()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            cls._disable_multiverse_overrides()

    @classmethod
    def _create_tenant(cls):
        tenant_model = get_tenant_model()
        database_name = (
            cls.tenant_database_name
            or multiverse_settings.tenant_database_name
            or cls.tenant_subdomain
        )

        tenant, _ = tenant_model.objects.get_or_create(
            subdomain=cls.tenant_subdomain,
            defaults={'database_name': database_name},
        )

        return tenant

    @classmethod
    def _disable_multiverse_overrides(cls):
        for override in reversed(getattr(cls, '_multiverse_overrides', [])):
            override.disable()

        cls._multiverse_overrides = []

    def _pre_setup(self):
        super()._pre_setup()

        set_current_tenant(self.tenant)

        if isinstance(getattr(self, 'client', None), TenantClient):
            self.client.tenant = self.tenant

    def _post_teardown(self):
        # Released before the superclass tears the databases down, so nothing
        # in teardown can run against a tenant that is about to disappear.
        forget_current_tenant()

        if isinstance(getattr(self, 'client', None), TenantClient):
            self.client.tenant = None

        super()._post_teardown()


class TenantTestCase(TenantTestCaseMixin, TestCase):
    client_class = TenantClient

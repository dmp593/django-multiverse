from django.test import TestCase
from django.conf import settings

from multiverse.utils import get_tenant_model, guess_tenant_database_name
from multiverse.test.client import TenantClient
from multiverse.test.utls import set_test_environment


Tenant = get_tenant_model()


class TenantTestCaseMixin:
    tenant: Tenant = None
    databases = '__all__'

    def __init__(self, *args, **kwargs):
        # needed here as well due to the action of database migration in a testing environment
        set_test_environment(True)
        super().__init__(*args, **kwargs)

    @classmethod
    def add_allowed_host(cls, host):
        if host not in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS.append(host)

    @classmethod
    def remove_allowed_host(cls, host):
        if host in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS.remove(host)

    @classmethod
    def setUpClass(cls):
        set_test_environment(True)

        subdomain = getattr(cls.tenant, 'subdomain', 'test')
        database_name = getattr(cls.tenant, 'database_name', guess_tenant_database_name())

        cls.tenant, _ = Tenant.objects.get_or_create(subdomain=subdomain, database_name=database_name)
        cls.add_allowed_host(cls.tenant.subdomain)

        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()

        if cls.tenant:
            cls.remove_allowed_host(cls.tenant.subdomain)
            cls.tenant = None

        set_test_environment(False)

    def _pre_setup(self):
        super()._pre_setup()

        if hasattr(self, 'client') and isinstance(self.client, TenantClient):
            self.client.tenant = self.tenant

    def _post_teardown(self):
        if hasattr(self, 'client') and isinstance(self.client, TenantClient):
            self.client.tenant = None

        super()._post_teardown()


class TenantTestCase(TenantTestCaseMixin, TestCase):
    client_class = TenantClient

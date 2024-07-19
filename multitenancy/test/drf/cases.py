from rest_framework.test import APITestCase

from multiverse.test.cases import TenantTestCaseMixin
from multiverse.test.drf.client import TenantAPIClient


class TenantAPITestCase(TenantTestCaseMixin, APITestCase):
    client_class = TenantAPIClient

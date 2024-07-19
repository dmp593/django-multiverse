from rest_framework.test import APIClient

from multiverse.test.client import TenantClient


class TenantAPIClient(TenantClient, APIClient):
    pass

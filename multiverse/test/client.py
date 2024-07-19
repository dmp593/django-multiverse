from django.test import RequestFactory, Client
from django.http import HttpRequest

from multiverse.utils import get_tenant_model


Tenant = get_tenant_model()


class TenantRequestFactory(RequestFactory):
    tenant: Tenant

    def generic(self, *args, **kwargs):
        if "HTTP_HOST" not in kwargs:
            kwargs["HTTP_HOST"] = self.tenant.subdomain

        request = super().generic(*args, **kwargs)
        request.tenant = self.tenant

        return request


class TenantClientMixin(TenantRequestFactory):
    def login(self, **credentials):
        request = HttpRequest()
        request.META['HTTP_HOST'] = self.tenant.subdomain
        request.tenant = self.tenant

        return super().login(**credentials)


class TenantClient(TenantClientMixin, Client):
    pass

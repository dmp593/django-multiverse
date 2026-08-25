"""
Test clients that address requests to a tenant.

The tenant model is resolved lazily, per request, rather than at import time.
Resolving it when the module loads froze the model before ``override_settings``
could swap it and made the import order of this package significant.
"""

from __future__ import annotations

from django.test import Client, RequestFactory


class TenantRequestFactory(RequestFactory):
    """Request factory that sends every request to :attr:`tenant`."""

    #: Tenant these requests are addressed to. Set by ``TenantTestCase``.
    tenant = None

    def generic(self, *args, **kwargs):
        if 'HTTP_HOST' not in kwargs and self.tenant is not None:
            kwargs['HTTP_HOST'] = self.tenant.subdomain

        request = super().generic(*args, **kwargs)
        request.tenant = self.tenant

        return request


class TenantClientMixin(TenantRequestFactory):
    pass


class TenantClient(TenantClientMixin, Client):
    pass
